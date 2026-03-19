from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import render
from django.utils.text import slugify
from django.views import View
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from config import Config
from videofetch_app.forms import UrlSender
from videofetch_app.manager import (
    enqueue_web_job_guarded,
    get_or_create_site_user,
    load_recent_jobs,
    select_and_hold,
    slot_to_free,
)
from videofetch_app.models import Job
from videofetch_app.presentation import (
    build_analysis_payload,
    build_audio_only_choice_format,
    json_safe,
    serialize_job,
)
from videofetch_app.serializers import AnalyzeRequestSerializer, StartJobRequestSerializer
from videofetcher.initialize import get_videofetcher
from videofetcher.service import normalize_audio_tracks


logger = logging.getLogger(__name__)
video_service = get_videofetcher()

PENDING_EXTRACT_SESSION_KEY = "site_pending_extract"
ANALYSIS_SLOT_TIMEOUT_SECONDS = 5 * 60
ERROR_MESSAGES = {
    "video_info_error": "Не удалось получить информацию о видео.",
    "no_formats_found": "Не удалось найти доступные форматы видео.",
    "no_video_found": "Не удалось найти видео на странице.",
    "youtube_auth_required": "YouTube запросил подтверждение или авторизацию.",
    "ytdlp_error": "Ошибка во время обработки источника главной библиотекой.",
}


class MainScreen(View):
    async def get(self, request, *args, **kwargs):
        user = await sync_to_async(get_or_create_site_user, thread_sensitive=True)(request)
        jobs = [serialize_job(job) for job in await sync_to_async(load_recent_jobs, thread_sensitive=True)(user)]
        return await sync_to_async(render, thread_sensitive=True)(
            request,
            "videofetch_app/index.html",
            {
                "form": UrlSender(),
                "chat": jobs,
                "has_chat": bool(jobs),
            },
        )


class SiteApiView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class AnalyzeApiView(SiteApiView):
    def post(self, request):
        get_or_create_site_user(request)

        serializer = AnalyzeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            errors = [str(err) for err in serializer.errors.get("url", [])] or ["Введите корректную ссылку."]
            return Response(
                {"status": "error", "message": errors[0], "errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = serializer.validated_data["url"].strip()
        now_ts = time.time()
        cooldown_s = float(getattr(Config, "ANALYSIS_USER_COOLDOWN_SECONDS", 3))
        last_ts = float(request.session.get("site_last_analysis_ts", 0) or 0)
        if cooldown_s > 0 and last_ts and now_ts < last_ts + cooldown_s:
            retry_after = max(1, int((last_ts + cooldown_s) - now_ts))
            return Response(
                {
                    "status": "rate_limited",
                    "message": f"Подождите {retry_after} сек. перед следующей проверкой.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        slot_id = select_and_hold(ANALYSIS_SLOT_TIMEOUT_SECONDS)
        if slot_id is None:
            return Response(
                {
                    "status": "busy",
                    "message": "Сервис анализа сейчас занят. Повторите попытку чуть позже.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        request.session["site_last_analysis_ts"] = now_ts

        try:
            video_info, formats, extract_status = async_to_sync(video_service.extract_video_info)(url)
        except Exception:
            logger.exception("Site analyze failed for %s", url)
            return Response(
                {
                    "status": "error",
                    "message": "Во время анализа произошла внутренняя ошибка.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            slot_to_free(slot_id)

        if not (extract_status == "success" and video_info and formats):
            base_status = extract_status.split(":", 1)[0] if isinstance(extract_status, str) and ":" in extract_status else extract_status
            return Response(
                {
                    "status": "error",
                    "message": ERROR_MESSAGES.get(base_status, f"Ошибка: {base_status}"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        prepared_formats = []
        for fmt in formats:
            if isinstance(fmt, dict) and fmt.get("audio_tracks"):
                fmt = dict(fmt)
                fmt["audio_tracks"] = normalize_audio_tracks(fmt.get("audio_tracks", []))
            prepared_formats.append(fmt)

        audio_only_choice = build_audio_only_choice_format(prepared_formats, video_info, url)
        if audio_only_choice:
            prepared_formats = list(prepared_formats) + [audio_only_choice]

        safe_video_info = json_safe(video_info)
        safe_formats = json_safe(prepared_formats)
        request.session[PENDING_EXTRACT_SESSION_KEY] = {
            "source_url": url,
            "video_info": safe_video_info,
            "formats": safe_formats,
            "created_at": now_ts,
        }
        request.session.save()

        return Response(
            {
                "status": "success",
                "analysis": build_analysis_payload(safe_video_info, safe_formats, url),
            }
        )


class StartJobApiView(SiteApiView):
    def post(self, request):
        user = get_or_create_site_user(request)
        state = request.session.get(PENDING_EXTRACT_SESSION_KEY) or {}
        if not state:
            return Response(
                {
                    "status": "error",
                    "message": "Данные анализа устарели. Отправьте ссылку снова.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = StartJobRequestSerializer(data=request.data)
        if not serializer.is_valid():
            error_map = serializer.errors
            if "format_index" in error_map:
                return Response(
                    {"status": "error", "message": "Не выбран формат."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if "audio_index" in error_map:
                return Response(
                    {"status": "error", "message": "Неверный выбор озвучки."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"status": "error", "message": "Некорректный запрос."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        format_index = serializer.validated_data["format_index"]
        formats = state.get("formats") or []
        if format_index < 0 or format_index >= len(formats):
            return Response(
                {"status": "error", "message": "Выбран несуществующий формат."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        video_info = state.get("video_info") or {}
        selected_format = dict(formats[format_index])
        audio_tracks = normalize_audio_tracks(selected_format.get("audio_tracks", []))
        selected_format["audio_tracks"] = audio_tracks

        audio_index = serializer.validated_data.get("audio_index")
        selected_audio = None
        if audio_tracks:
            if audio_index is None:
                return Response(
                    {
                        "status": "audio_required",
                        "message": "Для этого варианта нужно выбрать озвучку.",
                        "audio_tracks": [
                            {"index": idx, "name": track.get("name", f"Аудио {idx + 1}")}
                            for idx, track in enumerate(audio_tracks)
                        ],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if audio_index < 0 or audio_index >= len(audio_tracks):
                return Response(
                    {"status": "error", "message": "Озвучка не найдена."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            selected_audio = dict(audio_tracks[audio_index])

        if selected_format.get("is_audio_choice_button"):
            selected_format = dict(selected_format)
            selected_format["is_audio"] = True
            selected_format["is_hls_audio_only"] = bool(selected_format.get("is_hls_audio_only"))
            selected_format["quality"] = "Аудио"

        title = video_info.get("title") or ("Фильм" if video_info.get("is_movie") else "Видео")
        duration_seconds = int(video_info.get("duration") or 0)
        is_short = bool(duration_seconds and duration_seconds <= Config.SHORT_MAX_SECONDS)
        source_url = (
            (video_info.get("webpage_url") or video_info.get("url") or state.get("source_url") or selected_format.get("webpage_url") or "")
            .strip()
        )
        requested_quality = selected_format.get("quality") or selected_format.get("format_id") or "Видео"
        requested_audio = selected_audio.get("name") if selected_audio else None

        enqueue_status, job, active_jobs = enqueue_web_job_guarded(
            user=user,
            source_url=source_url,
            title=title,
            duration_seconds=duration_seconds,
            is_short=is_short,
            requested_quality=requested_quality,
            requested_audio=requested_audio,
            selected_format=json_safe(selected_format),
            selected_audio=json_safe(selected_audio),
        )

        if enqueue_status == "duplicate":
            return Response(
                {
                    "status": "duplicate",
                    "message": "У вас уже есть активная задача для этой ссылки.",
                    "job": serialize_job(job),
                }
            )
        if enqueue_status == "limit_reached":
            return Response(
                {
                    "status": "limit_reached",
                    "message": f"У вас уже есть {active_jobs} активная задача. Дождитесь завершения текущей загрузки.",
                    "job": serialize_job(job) if job else None,
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "status": "enqueued",
                "message": "Задача поставлена в очередь.",
                "job": serialize_job(job),
            },
            status=status.HTTP_201_CREATED,
        )


class JobStatusApiView(SiteApiView):
    def get(self, request, job_id: int):
        user = get_or_create_site_user(request)
        job = Job.objects.filter(id=job_id, created_by_user=user).first()
        if job is None:
            return Response(
                {"status": "error", "message": "Задача не найдена."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"status": "success", "job": serialize_job(job)})


api_analyze = AnalyzeApiView.as_view()
api_start_job = StartJobApiView.as_view()
api_job_status = JobStatusApiView.as_view()


async def _stream_file_chunks(file_path: Path, chunk_size: int = 1024 * 1024):
    file_handle = await sync_to_async(file_path.open, thread_sensitive=True)("rb")
    try:
        while True:
            chunk = await sync_to_async(file_handle.read, thread_sensitive=True)(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        await sync_to_async(file_handle.close, thread_sensitive=True)()


@require_GET
async def job_download(request, job_id: int):
    user = await sync_to_async(get_or_create_site_user, thread_sensitive=True)(request)
    job = await Job.objects.filter(id=job_id, created_by_user=user, status=Job.Status.DONE).afirst()
    if job is None or not job.result_path:
        raise Http404("File not found")

    file_path = Path(job.result_path).expanduser()
    if not file_path.is_absolute():
        file_path = Path(settings.PROJECT_ROOT) / file_path
    file_path = file_path.resolve()
    if not file_path.exists() or not file_path.is_file():
        raise Http404("File not found")

    if not file_path.name.startswith(f"{job.id}_"):
        raise Http404("Invalid file")

    temp_root = Path(Config.TEMP_DIR)
    if not temp_root.is_absolute():
        temp_root = Path(settings.PROJECT_ROOT) / temp_root
    temp_root = temp_root.resolve()
    if temp_root not in file_path.parents:
        raise Http404("Invalid file")

    base_name = slugify(job.title or "download") or "download"
    filename = f"{base_name}{file_path.suffix or '.bin'}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = StreamingHttpResponse(
        _stream_file_chunks(file_path),
        content_type=content_type,
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(file_path.stat().st_size)
    return response
