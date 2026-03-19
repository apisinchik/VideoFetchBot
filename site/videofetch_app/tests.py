from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.datastructures import MultiValueDict
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from bot.broadcast_media import build_broadcast_send_plan, classify_broadcast_attachment
from bot.broadcast_manager import split_broadcast_text
from videofetch_app.forms import BroadcastAdminForm
from videofetcher.service import VideoService
from videofetch_app.presentation import build_analysis_payload, build_visible_format_choices, serialize_job
from videofetch_app.views import MainScreen, api_analyze, api_start_job, job_download


@override_settings(
    ROOT_URLCONF="videofetch_site.urls",
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
)
class SiteFlowTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request_with_session(self, method: str, path: str, *, data: dict | None = None):
        body = json.dumps(data or {})
        request = getattr(self.factory, method.lower())(
            path,
            data=body,
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def _render_response(self, response):
        if hasattr(response, "render"):
            response.render()
        return response

    def test_build_visible_format_choices_keeps_video_and_audio(self):
        formats = [
            {
                "quality": "1280",
                "filesize_raw": 1400 * 1024 * 1024,
                "audio_tracks": [{"name": "Рус. Дублированный"}],
            },
            {
                "quality": "Аудио",
                "is_audio": True,
                "filesize_raw": 55 * 1024 * 1024,
            },
        ]

        choices = build_visible_format_choices(formats, duration=7200)

        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0]["kind"], "video")
        self.assertIn("1280", choices[0]["label"])
        self.assertEqual(choices[1]["kind"], "audio")
        self.assertIn("Аудио", choices[1]["label"])

    def test_build_analysis_payload_counts_audio_tracks(self):
        payload = build_analysis_payload(
            {"title": "Terminal", "duration": 7725, "is_movie": True},
            [{"quality": "1280", "audio_tracks": [{"name": "Рус. Дублированный"}, {"name": "Eng.Original"}]}],
            "https://example.com/video",
        )

        self.assertEqual(payload["title"], "Terminal")
        self.assertEqual(payload["total_audio_tracks"], 2)
        self.assertEqual(payload["video_option_count"], 1)

    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_api_analyze_rejects_private_url(self, _user_mock):
        request = self._request_with_session("post", "/api/analyze/", data={"url": "http://127.0.0.1:8000/admin"})
        response = self._render_response(api_analyze(request))

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "error")
        self.assertIn("публичные", payload["message"])

    @patch("videofetch_app.views.slot_to_free")
    @patch("videofetch_app.views.select_and_hold", return_value=1)
    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_api_analyze_stores_extract_state(self, _user_mock, _slot_mock, _slot_to_free_mock):
        async def fake_extract(_url):
            return (
                {"title": "Terminal", "duration": 7725, "is_movie": True, "webpage_url": "https://example.com/video"},
                [
                    {
                        "quality": "1280",
                        "filesize_raw": 1400 * 1024 * 1024,
                        "audio_tracks": [{"name": "Рус. Дублированный"}, {"name": "Eng.Original"}],
                    }
                ],
                "success",
            )

        request = self._request_with_session("post", "/api/analyze/", data={"url": "https://example.com/video"})
        with patch("videofetch_app.views.video_service.extract_video_info", new=fake_extract):
            response = self._render_response(api_analyze(request))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["analysis"]["formats"])
        self.assertIn("site_pending_extract", request.session)
        self.assertEqual(request.session["site_pending_extract"]["source_url"], "https://example.com/video")

    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_api_start_job_requires_audio_choice(self, _user_mock):
        request = self._request_with_session("post", "/api/jobs/start/", data={"format_index": 0})
        request.session["site_pending_extract"] = {
            "source_url": "https://example.com/video",
            "video_info": {"title": "Terminal", "duration": 7725},
            "formats": [{"quality": "1280", "audio_tracks": [{"name": "Рус. Дублированный"}]}],
        }
        request.session.save()
        response = self._render_response(api_start_job(request))

        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "audio_required")
        self.assertEqual(payload["audio_tracks"][0]["name"], "Рус. Дублированный")

    @patch("videofetch_app.views.serialize_job", return_value={"id": 42, "status": "queued", "source_url": "https://example.com/video"})
    @patch("videofetch_app.views.enqueue_web_job_guarded")
    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_api_start_job_enqueues_selected_audio(self, _user_mock, enqueue_mock, _serialize_mock):
        enqueue_mock.return_value = ("enqueued", SimpleNamespace(id=42), 1)
        request = self._request_with_session(
            "post",
            "/api/jobs/start/",
            data={"format_index": 0, "audio_index": 1},
        )
        request.session["site_pending_extract"] = {
            "source_url": "https://example.com/video",
            "video_info": {"title": "Terminal", "duration": 7725, "webpage_url": "https://example.com/video"},
            "formats": [{"quality": "1280", "audio_tracks": [{"name": "Рус. Дублированный"}, {"name": "Eng.Original"}]}],
        }
        request.session.save()
        response = self._render_response(api_start_job(request))

        self.assertEqual(response.status_code, 201)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "enqueued")
        enqueue_kwargs = enqueue_mock.call_args.kwargs
        self.assertEqual(enqueue_kwargs["requested_audio"], "Eng.Original")
        self.assertEqual(enqueue_kwargs["requested_quality"], "1280")

    @patch("videofetch_app.views.load_recent_jobs", return_value=[])
    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_main_screen_renders_page(self, _user_mock, _jobs_mock):
        request = self.factory.get("/")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        response = async_to_sync(MainScreen.as_view())(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'js-analyze-form', response.content)

    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_job_download_rejects_path_outside_temp(self, _user_mock):
        request = self.factory.get("/jobs/5/download/")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        fake_job = SimpleNamespace(
            id=5,
            status="done",
            result_path="/etc/passwd",
            title="Terminal",
        )

        with patch("videofetch_app.views.Job.objects.filter") as filter_mock:
            filter_mock.return_value.afirst = AsyncMock(return_value=fake_job)
            with self.assertRaises(Http404):
                async_to_sync(job_download)(request, 5)

    @patch("videofetch_app.views.get_or_create_site_user", return_value=SimpleNamespace(id=1))
    def test_job_download_returns_file_from_temp(self, _user_mock):
        request = self.factory.get("/jobs/42/download/")
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

        temp_dir = Path("/home/algol83/PycharmProjects/VideoFetchBot/temp")
        temp_dir.mkdir(exist_ok=True)
        file_path = temp_dir / "42_result.mp4"
        file_path.write_bytes(b"video")

        fake_job = SimpleNamespace(
            id=42,
            status="done",
            result_path="temp/42_result.mp4",
            title="Terminal 2004",
        )

        try:
            with patch("videofetch_app.views.Job.objects.filter") as filter_mock:
                filter_mock.return_value.afirst = AsyncMock(return_value=fake_job)
                response = async_to_sync(job_download)(request, 42)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Disposition"].split("filename=")[-1].strip('"'), "terminal-2004.mp4")
        finally:
            file_path.unlink(missing_ok=True)

    def test_serialize_job_hides_internal_error_details(self):
        job = SimpleNamespace(
            id=9,
            source_url="https://example.com/video",
            title="Terminal",
            duration_seconds=10,
            requested_quality="1280",
            requested_audio=None,
            selected_format={},
            selected_audio={},
            status="failed",
            stage="failed",
            progress=0,
            result_size_bytes=None,
            result_path=None,
            error_message="download_video returned empty path: /abs/path/secret",
        )

        payload = serialize_job(job)

        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("/abs/path/secret", payload["error_message"])


class VideoServiceExtractionFlowTests(SimpleTestCase):
    def test_cookie_refresh_skipped_for_non_youtube_url(self):
        service = VideoService()
        service._ytdlp_auto_refresh_cookies = True
        service._ytdlp_cookies_file = None

        with patch.object(service, "_load_cookies_manager") as manager_mock:
            refreshed = async_to_sync(service._refresh_cookiefile)("https://example.com/film/terminal-2004/")

        self.assertFalse(refreshed)
        manager_mock.assert_not_called()

    def test_non_youtube_page_continues_after_embedded_youtube_auth_error(self):
        service = VideoService()
        service.playwright_available = True
        service.settings.enable_browser_fallback = True

        with (
            patch.object(
                service,
                "_extract_with_ytdlp",
                AsyncMock(return_value=(None, None, "youtube_auth_required")),
            ),
            patch.object(
                service,
                "_extract_with_enhanced_analysis",
                AsyncMock(return_value=({"title": "Film"}, [{"quality": "1280"}], "success")),
            ) as browser_mock,
        ):
            info, formats, status_name = async_to_sync(service._extract_video_info_once)(
                "https://example.com/film/terminal-2004/",
                use_proxy=False,
            )

        self.assertEqual(status_name, "success")
        self.assertEqual(info["title"], "Film")
        self.assertEqual(formats[0]["quality"], "1280")
        browser_mock.assert_awaited_once()

    def test_direct_youtube_url_stops_on_auth_error(self):
        service = VideoService()
        service.playwright_available = True
        service.settings.enable_browser_fallback = True

        with (
            patch.object(
                service,
                "_extract_with_ytdlp",
                AsyncMock(return_value=(None, None, "youtube_auth_required")),
            ),
            patch.object(service, "_extract_with_enhanced_analysis", AsyncMock()) as browser_mock,
        ):
            _info, _formats, status_name = async_to_sync(service._extract_video_info_once)(
                "https://www.youtube.com/watch?v=atjlJLvOQQQ",
                use_proxy=False,
            )

        self.assertEqual(status_name, "youtube_auth_required")
        browser_mock.assert_not_awaited()


class BroadcastHelpersTests(SimpleTestCase):
    def test_split_broadcast_text_chunks_long_message(self):
        text = ("alpha " * 900).strip()

        chunks = split_broadcast_text(text, limit=512)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 512 for chunk in chunks))
        self.assertEqual(" ".join(chunks).replace("  ", " "), text)

    def test_broadcast_admin_form_uses_multi_file_widget(self):
        form = BroadcastAdminForm()

        self.assertIn("upload_files", form.fields)
        self.assertTrue(form.fields["upload_files"].widget.allow_multiple_selected)

    def test_broadcast_admin_form_accepts_multiple_files(self):
        files = [
            SimpleUploadedFile('a.txt', b'a', content_type='text/plain'),
            SimpleUploadedFile('b.txt', b'b', content_type='text/plain'),
        ]
        form = BroadcastAdminForm(
            data={'title': 'Promo', 'text': 'Body', 'recipient_mode': 'all_telegram'},
            files=MultiValueDict({'upload_files': files}),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data['upload_files']), 2)

    def test_classify_broadcast_attachment_prefers_photo_and_video_media(self):
        photo = SimpleNamespace(original_name='poster.jpg', content_type='image/jpeg')
        video = SimpleNamespace(original_name='trailer.mp4', content_type='video/mp4')
        doc = SimpleNamespace(original_name='price.pdf', content_type='application/pdf')

        self.assertEqual(classify_broadcast_attachment(photo), 'photo')
        self.assertEqual(classify_broadcast_attachment(video), 'video')
        self.assertEqual(classify_broadcast_attachment(doc), 'document')

    def test_build_broadcast_send_plan_uses_single_media_with_caption(self):
        attachment = SimpleNamespace(original_name='poster.jpg', content_type='image/jpeg')

        plan = build_broadcast_send_plan('Promo text', [attachment])

        self.assertEqual(plan.mode, 'single_photo')
        self.assertEqual(plan.caption, 'Promo text')

    def test_build_broadcast_send_plan_rejects_mixed_document_and_media(self):
        attachments = [
            SimpleNamespace(original_name='poster.jpg', content_type='image/jpeg'),
            SimpleNamespace(original_name='brief.pdf', content_type='application/pdf'),
        ]

        with self.assertRaises(ValueError):
            build_broadcast_send_plan('Promo text', attachments)

    def test_build_broadcast_send_plan_rejects_long_caption_for_media(self):
        attachment = SimpleNamespace(original_name='poster.jpg', content_type='image/jpeg')

        with self.assertRaises(ValueError):
            build_broadcast_send_plan('x' * 1025, [attachment])
