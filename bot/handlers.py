from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge, TelegramNetworkError
from aiogram import Bot
import os
import logging
import asyncio
from typing import Dict, Tuple
import time
import re
from videofetcher.service import normalize_audio_tracks
from videofetcher.url_safety import is_public_http_url
from bot.media_utils import estimate_voice_size_bytes

from config import Config
from db.postgres_queue import (
    upsert_telegram_user,
    get_active_job_for_user,
    enqueue_job_guarded,
    get_queue_position,
    count_running,
    cancel_latest_queued,
    select_and_hold,
    slot_to_free
)

router = Router()
logger = logging.getLogger(__name__)

user_video_data = {}
user_audio_selections = {}

last_progress_state: Dict[Tuple[int, int], Dict] = {}

active_downloads = set()
active_analysis_users = set()
active_retry_users = set()
analysis_cooldowns: Dict[int, float] = {}
retry_cooldowns: Dict[int, float] = {}



def create_progress_bar(progress: int, length: int = 10) -> str:
    """Создает текстовый прогресс-бар"""
    filled = int(length * progress / 100)
    empty = length - filled
    return '🟩' * filled + '⬜' * (empty)


def format_time(seconds: float) -> str:
    """Форматирует время в читаемый формат"""
    if seconds <= 0:
        return "00:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def _audio_tracks_look_technical(audio_tracks) -> bool:
    """Возвращает True, если названия дорожек выглядят как технические rus0/eng1/audio2."""
    if not isinstance(audio_tracks, list) or not audio_tracks:
        return True

    technical_re = re.compile(r"^(?:audio|rus|ukr|eng|fra|ger|deu|ita|spa)\d+$", re.IGNORECASE)
    seen_meaningful = False

    for i, track in enumerate(audio_tracks):
        if not isinstance(track, dict):
            continue
        name = (
            (track.get("name") or "").strip()
            or (track.get("display_name") or "").strip()
            or (track.get("technical_name") or "").strip()
            or f"audio{i}"
        )
        if not name:
            continue
        if not technical_re.fullmatch(name):
            return False
        seen_meaningful = True

    return seen_meaningful


def _remaining_cooldown(last_ts: float, cooldown_s: float) -> int:
    if not last_ts or cooldown_s <= 0:
        return 0
    left = int((last_ts + cooldown_s) - time.time())
    return max(0, left)


def _build_audio_only_choice_format(formats, video_info, original_url: str) -> Dict | None:
    if not isinstance(formats, list):
        return None

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        audio_tracks = normalize_audio_tracks(fmt.get("audio_tracks", []))
        if not audio_tracks:
            continue

        master_url = (fmt.get("master_url") or fmt.get("url") or "").strip()
        page_url = (
            (video_info.get("webpage_url") if isinstance(video_info, dict) else None)
            or original_url
            or fmt.get("webpage_url")
            or master_url
        )
        if not master_url:
            continue

        return {
            "format_id": f"{fmt.get('format_id') or 'hls'}_audio_only",
            "quality": "Аудио",
            "filesize": "~",
            "filesize_raw": estimate_voice_size_bytes(
                int(fmt.get("duration") or (video_info.get("duration") if isinstance(video_info, dict) else 0) or 0)
            ),
            "ext": "m4a",
            "url": master_url,
            "master_url": master_url,
            "webpage_url": page_url,
            "duration": int(fmt.get("duration") or (video_info.get("duration") if isinstance(video_info, dict) else 0) or 0),
            "is_audio": True,
            "is_audio_choice_button": True,
            "is_hls_audio_only": True,
            "audio_tracks": audio_tracks,
        }

    return None


async def update_progress_message(bot: Bot, chat_id: int, message_id: int, progress: float, start_time: float = None):
    """Обновляет сообщение с прогрессом загрузки с проверкой на изменение контента"""
    try:
        current_progress = int(progress)

        key = (chat_id, message_id)

        current_time = time.time()
        elapsed_time = current_time - start_time if start_time else 0

        remaining_time = 0
        if current_progress > 0 and elapsed_time > 0:
            total_estimated = elapsed_time / (current_progress / 100)
            remaining_time = total_estimated - elapsed_time

        elapsed_str = format_time(elapsed_time)
        remaining_str = format_time(remaining_time)

        progress_bar = create_progress_bar(current_progress)

        new_text = f"📥 **Загрузка видео...**\n\n{progress_bar} {current_progress}%\n\n⏱️ Прошло: {elapsed_str}\n⏳ Осталось: {remaining_str}"

        last_state = last_progress_state.get(key, {})
        last_text = last_state.get('text', '')
        last_progress = last_state.get('progress', -1)

        should_update = (
                current_progress != last_progress or
                new_text != last_text or
                current_progress == 100
        )

        if not should_update:
            return

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text
            )

            last_progress_state[key] = {
                'text': new_text,
                'progress': current_progress,
                'timestamp': current_time
            }

            logger.debug(f"Прогресс обновлен: {current_progress}%")

        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                logger.debug("Сообщение не требует обновления")
            else:
                logger.error(f"Ошибка при обновлении прогресса: {e}")
        except Exception as e:
            logger.error(f"Другая ошибка при обновлении прогресса: {e}")

    except Exception as e:
        logger.error(f"Критическая ошибка в update_progress_message: {e}")


async def cleanup_progress_state(chat_id: int, message_id: int):
    """Очищает состояние прогресса для указанного сообщения"""
    key = (chat_id, message_id)
    if key in last_progress_state:
        del last_progress_state[key]
        logger.debug(f"Состояние прогресса очищено для {key}")


@router.message(Command("start"))
async def start_cmd(message: Message):
    welcome_text = """
🎬 **Добро пожаловать в Video Downloader Bot!**

📋 **Как использовать:**
1. Отправьте ссылку на видео или фильм
2. Выберите желаемое качество
3. Выберите озвучку (если доступно)
4. Получите видео!

🚀 **Особенности:**
• Поддержка YouTube и онлайн-кинотеатров
• Полный сбор всех качеств и озвучек
• Прогресс загрузки в реальном времени
• Автоматический выбор лучшего качества

💡 **Платформы:** YouTube и сайты с HLS/DASH плеерами (где удаётся извлечь поток).
    """
    await message.answer(welcome_text)


@router.message(F.text)
async def handle_text(message: Message, bot: Bot):
    slot_id = None
    db_pool = getattr(bot, 'db_pool', None)
    user_id = message.from_user.id
    try:
        text = message.text.strip()

        if text.startswith('/'):
            return

        if not text.startswith(('http://', 'https://')):
            await message.answer("❌ Пожалуйста, отправьте корректную ссылку на видео")
            return

        if not is_public_http_url(text):
            await message.answer("❌ Разрешены только публичные http(s)-ссылки.")
            return

        if user_id in active_analysis_users:
            await message.answer("⏳ Анализ предыдущей ссылки ещё не завершён.")
            return

        cooldown_left = _remaining_cooldown(
            analysis_cooldowns.get(user_id, 0.0),
            float(getattr(Config, "ANALYSIS_USER_COOLDOWN_SECONDS", 0)),
        )
        if cooldown_left > 0:
            await message.answer(f"⏳ Подождите {cooldown_left}с перед новой ссылкой.")
            return

        active_analysis_users.add(user_id)
        analysis_cooldowns[user_id] = time.time()

        u = text.lower()
        is_movie = ('/embed/' in u) or ('initem.ws' in u)

        if is_movie:
            progress_msg = await message.answer(
                "🎬 Обнаружен фильм...\n\n⏳ Загружаю через расширенный анализ...\n\nЭто может занять до 60 секунд...")
        else:
            progress_msg = await message.answer("🔍 Анализирую ссылку...")

        video_service = getattr(bot, 'video_service', None)
        if not video_service:
            await progress_msg.edit_text("❌ Сервис видео не доступен. Попробуйте позже.")
            return

        slot_id = await select_and_hold(db_pool, 5*60)
        while slot_id is None:
            await asyncio.sleep(3)
            slot_id = await select_and_hold(db_pool, 5*60)
            
        video_info, formats, status = await video_service.extract_video_info(text)

        await slot_to_free(db_pool, slot_id)

        if (status == "success" and video_info and formats and len(formats) > 0):
            if formats:
                for _f in formats:
                    if isinstance(_f, dict) and _f.get('audio_tracks'):
                        _f['audio_tracks'] = normalize_audio_tracks(_f.get('audio_tracks', []))

            audio_only_choice = _build_audio_only_choice_format(formats, video_info, text)
            if audio_only_choice:
                formats = list(formats) + [audio_only_choice]

            user_video_data[message.from_user.id] = {
                'formats': formats,
                'video_info': video_info,
                'original_url': text,
                'is_movie': video_info.get('is_movie', False)
            }

            duration_seconds = video_info.get('duration', 0)
            if duration_seconds:
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                if minutes > 60:
                    hours = minutes // 60
                    minutes = minutes % 60
                    duration_text = f"{hours}ч {minutes}мин {seconds}сек"
                else:
                    duration_text = f"{minutes}мин {seconds}сек"
            else:
                duration_text = "Неизвестно"

            audio_tracks = []
            for _f in formats:
                if isinstance(_f, dict) and _f.get('audio_tracks'):
                    audio_tracks = normalize_audio_tracks(_f.get('audio_tracks', []))
                    break
            total_audio_tracks = len(audio_tracks)
            video_options_count = sum(
                1 for _f in formats
                if isinstance(_f, dict) and not _f.get('is_audio') and not _f.get('is_audio_choice_button')
            )
            if video_options_count <= 0:
                video_options_count = len(formats)

            title = video_info.get('title') or ("Фильм" if video_info.get('is_movie') else "Видео")

            if video_info.get('is_movie') or total_audio_tracks > 0:
                preview_text = f"""
🎬 **{title}**

⏱️ **Длительность:** {duration_text}
🎯 **Доступно качеств:** {video_options_count}
🎵 **Аудиодорожек:** {total_audio_tracks}

👇 **Выберите качество или аудио для скачивания:**
"""
            else:
                preview_text = f"""
🎬 **{title}**

⏱️ **Длительность:** {duration_text}
🎯 **Доступно форматов:** {video_options_count}

👇 **Выберите качество или аудио для скачивания:**
"""

            keyboard = None
            try:
                from bot.keyboards import create_quality_keyboard
                keyboard = create_quality_keyboard(formats, duration=duration_seconds)
            except ImportError as e:
                logger.error(f"Не удалось импортировать модуль keyboards: {e}")
            except Exception as e:
                logger.error(f"Ошибка при создании клавиатуры: {e}")

            if keyboard:
                await progress_msg.edit_text(
                    preview_text,
                    reply_markup=keyboard
                )
            else:
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard_buttons = []

                for i, fmt in enumerate(formats[:10]):
                    quality = fmt.get('quality', 'Unknown')
                    if not quality or quality == 'Unknown':
                        quality = fmt.get('format_note', fmt.get('format', f'Format {i + 1}'))

                    if len(quality) > 30:
                        quality = quality[:27] + "..."

                    keyboard_buttons.append([
                        InlineKeyboardButton(
                            text=f"🎯 {quality}",
                            callback_data=f"quality:{i}"
                        )
                    ])

                if keyboard_buttons:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                    await progress_msg.edit_text(
                        preview_text,
                        reply_markup=keyboard
                    )
                else:
                    format_list = "\n".join([
                        f"{i + 1}. {fmt.get('quality', 'Unknown')}"
                        for i, fmt in enumerate(formats[:5])
                    ])
                    await progress_msg.edit_text(f"{preview_text}\n{format_list}")

        else:
            error_messages = {
                "video_info_error": "❌ Не удалось получить информацию о видео",
                "no_formats_found": "❌ Не удалось найти доступные форматы видео",
                "no_video_found": "❌ Не удалось найти видео на странице",
                "youtube_auth_required": "❌ YouTube запросил подтверждение/авторизацию.\n\nАдминистратор должен обновить cookies.txt (см. `YTDLP_COOKIES_FILE`) и повторить попытку.",
                "ytdlp_error": "Ошибка на стороне главной библиотеки"
            }

            base_status = status.split(":")[0] if ":" in status else status
            error_msg = error_messages.get(base_status, f"❌ Ошибка: {base_status}")

            await progress_msg.edit_text(error_msg)

    except Exception as e:
        if db_pool and slot_id is not None:
            await slot_to_free(db_pool, slot_id)
            logger.error(f"Unexpected error in handle_text: {str(e)}", exc_info=True)
        await message.answer("❌ Произошла непредвиденная ошибка при обработке запроса")
    finally:
        active_analysis_users.discard(user_id)


@router.callback_query(F.data.startswith("quality:"))
async def handle_quality_selection(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора качества"""
    try:
        await callback.answer()

        user_id = callback.from_user.id
        if user_id in active_downloads:
            await callback.answer("⏳ Ваш запрос уже обрабатывается...", show_alert=True)
            return

        data_parts = callback.data.split(":")
        if len(data_parts) < 2:
            await callback.message.answer("❌ Неверный формат данных")
            return

        quality_index_str = data_parts[1]

        user_data = user_video_data.get(callback.from_user.id)
        if not user_data:
            await callback.message.answer("❌ Данные устарели. Отправьте ссылку снова.")
            return

        formats = user_data.get('formats', [])
        video_info = user_data.get('video_info', {})
        if not formats:
            await callback.message.answer("❌ Ошибка в данных форматов")
            return

        try:
            quality_index = int(quality_index_str)
            if quality_index < 0 or quality_index >= len(formats):
                await callback.message.answer("❌ Неверный выбор качества")
                return

            selected_format = formats[quality_index]
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing quality index: {e}")
            await callback.message.answer("❌ Неверный выбор качества")
            return

        if selected_format.get('is_m3u8') and not selected_format.get('is_audio'):
            video_service = getattr(bot, 'video_service', None)
            master_url = (selected_format.get('master_url') or selected_format.get('url') or '').strip()
            referer = (
                (video_info.get('webpage_url') or user_data.get('original_url') or '').strip()
            )
            existing_tracks = normalize_audio_tracks(selected_format.get('audio_tracks', []))
            should_refresh_tracks = not existing_tracks or _audio_tracks_look_technical(existing_tracks)
            if should_refresh_tracks:
                logger.info("HLS audio tracks look technical or missing; trying to refresh from master playlist")
            else:
                logger.info("Keeping human-readable HLS audio tracks from extraction")
            proxy_url = None
            if video_service and video_service.use_proxy and video_service.proxy_url:
                proxy_url = video_service.proxy_url
            if master_url and should_refresh_tracks:
                try:
                    from videofetcher.service import HLSVideoDownloader

                    async with HLSVideoDownloader(proxy_url=proxy_url) as downloader:
                        info = await downloader.get_master_playlist_info(
                            master_url, referer=referer, page_content=None
                        )
                    if isinstance(info, dict):
                        tracks = info.get('audio_tracks', []) or []
                        refreshed_tracks = normalize_audio_tracks(tracks)
                        if refreshed_tracks and not _audio_tracks_look_technical(refreshed_tracks):
                            selected_format['audio_tracks'] = refreshed_tracks
                        elif not existing_tracks and refreshed_tracks:
                            selected_format['audio_tracks'] = refreshed_tracks
                except Exception as e:
                    logger.error(f"Failed to fetch audio tracks for HLS: {e}")

        if selected_format.get('audio_tracks') and (
            not selected_format.get('is_audio') or selected_format.get('is_audio_choice_button')
        ):
            user_audio_selections[callback.from_user.id] = {
                'format': selected_format,
                'video_info': video_info,
                'mode': 'audio_only' if selected_format.get('is_audio_choice_button') else 'video',
            }

            audio_tracks = normalize_audio_tracks(selected_format.get('audio_tracks', []))
            selected_format['audio_tracks'] = audio_tracks
            logger.info(f"Доступные аудиодорожки: {[track.get('name', 'Unknown') for track in audio_tracks]}")

            audio_text = f"""
        🎬 **{video_info.get('title', 'Фильм')}**

        ✅ **Выбрано качество:** {selected_format.get('quality', 'Unknown')}

        🎵 **Выберите озвучку:**
        """

            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard_buttons = []
            for i, track in enumerate(audio_tracks):
                track_name = track.get('name', f'Аудио {i + 1}')
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"🎵 {track_name}",
                        callback_data=f"audio:{i}"
                    )
                ])

            keyboard_buttons.append([
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

            await callback.message.edit_text(audio_text, reply_markup=keyboard)
            return

        await start_download(callback, bot, selected_format, video_info, None)

    except Exception as e:
        logger.error(f"Error in quality selection: {str(e)}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при обработке запроса")


@router.callback_query(F.data.startswith("audio:"))
async def handle_audio_selection(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора аудиодорожки"""
    try:
        await callback.answer()

        user_id = callback.from_user.id
        if user_id in active_downloads:
            await callback.answer("⏳ Ваш запрос уже обрабатывается...", show_alert=True)
            return

        data_parts = callback.data.split(":")
        if len(data_parts) < 2:
            await callback.message.answer("❌ Неверный формат данных")
            return

        audio_index_str = data_parts[1]

        user_selection = user_audio_selections.get(callback.from_user.id)
        if not user_selection:
            await callback.message.answer("❌ Данные устарели. Начните заново.")
            return

        selected_format = user_selection['format']
        video_info = user_selection['video_info']
        selection_mode = user_selection.get('mode', 'video')
        audio_tracks = normalize_audio_tracks(selected_format.get('audio_tracks', []))
        selected_format['audio_tracks'] = audio_tracks
        try:
            audio_index = int(audio_index_str)
            if audio_index == -1:
                selected_audio = None
            elif 0 <= audio_index < len(audio_tracks):
                selected_audio = audio_tracks[audio_index]
            else:
                await callback.message.answer("❌ Неверный выбор озвучки")
                return
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing audio index: {e}")
            await callback.message.answer("❌ Неверный выбор озвучки")
            return

        await callback.message.edit_reply_markup(reply_markup=None)

        try:
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Error deleting audio selection message: {e}")

        if selection_mode == 'audio_only':
            audio_only_format = dict(selected_format)
            audio_only_format['is_audio'] = True
            audio_only_format['is_hls_audio_only'] = bool(selected_format.get('is_hls_audio_only'))
            audio_only_format['quality'] = "Аудио"
            await start_download(callback, bot, audio_only_format, video_info, selected_audio)
            return

        await start_download(callback, bot, selected_format, video_info, selected_audio)

    except Exception as e:
        logger.error(f"Error in audio selection: {str(e)}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при обработке запроса")


async def start_download(callback: CallbackQuery, bot: Bot, selected_format: Dict, video_info: Dict,
                         audio_track: Dict = None):
    """Создаёт задачу скачивания в Postgres-очереди."""
    tg_user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    source_url = (
        video_info.get("webpage_url")
        or video_info.get("url")
        or selected_format.get("webpage_url")
        or ""
    ).strip()

    if tg_user_id in active_downloads:
        await callback.answer("⏳ Обрабатываю предыдущий запрос...", show_alert=False)
        return

    db_pool = getattr(bot, "db_pool", None)
    if not db_pool:
        await callback.message.answer("❌ PostgreSQL не инициализирован. Проверьте POSTGRES_DSN")
        return

    try:
        active_downloads.add(tg_user_id)

        user_id = await upsert_telegram_user(
            db_pool,
            telegram_user_id=tg_user_id,
            chat_id=chat_id,
            username=getattr(callback.from_user, "username", None),
            first_name=getattr(callback.from_user, "first_name", None),
            last_name=getattr(callback.from_user, "last_name", None),
            language_code=getattr(callback.from_user, "language_code", None),
        )

        max_active_jobs = int(getattr(Config, "USER_MAX_ACTIVE_JOBS", 1))
        title = video_info.get("title") or "Видео"
        duration = int(video_info.get("duration") or 0)
        is_short = bool(duration and duration <= Config.SHORT_MAX_SECONDS)

        quality = selected_format.get("quality") or "Unknown"
        requested_audio = audio_track.get("name") if audio_track else None

        lane = "короткая" if is_short else "общая"
        msg = await callback.message.answer(
            f"⏳ Добавляю задачу в **{lane}** очередь...\n\n🎬 **{title}**\n🎯 **Качество:** {quality}"
        )

        enqueue_result = await enqueue_job_guarded(
            db_pool,
            user_id=user_id,
            telegram_user_id=tg_user_id,
            telegram_chat_id=chat_id,
            progress_msg_id=msg.message_id,
            source_url=source_url,
            title=title,
            duration_seconds=duration,
            is_short=is_short,
            requested_quality=quality,
            requested_audio=requested_audio,
            selected_format=selected_format,
            selected_audio=audio_track,
            max_active_jobs=max_active_jobs,
        )

        if enqueue_result.status == "duplicate" and enqueue_result.existing_job:
            active_job = enqueue_result.existing_job
            if active_job.status == "queued":
                pos = await get_queue_position(db_pool, job_id=active_job.id)
                await msg.edit_text(
                    f"⏳ У вас уже есть задача в очереди для этого видео. Место: **#{pos or '?'}**"
                )
            else:
                await msg.edit_text("⏳ У вас уже идёт скачивание этого видео.")
            return

        if enqueue_result.status == "limit_reached":
            await msg.edit_text(
                f"⏳ У вас уже есть {enqueue_result.active_jobs} активн. задач(а). "
                f"Лимит: {max_active_jobs}. Дождитесь завершения или используйте /cancel."
            )
            return

        if enqueue_result.status != "enqueued" or not enqueue_result.job_id:
            await msg.edit_text("❌ Не удалось добавить задачу в очередь.")
            return

        job_id = enqueue_result.job_id

        pos = await get_queue_position(db_pool, job_id=job_id)
        running = await count_running(db_pool, is_short=is_short)
        slots = Config.QUEUE_SHORT_SLOTS if is_short else Config.QUEUE_GENERAL_SLOTS

        if pos == 1 and running < slots:
            text = (
                f"🚀 **Ставлю на выполнение прямо сейчас**\n\n"
                f"🎬 **{title}**\n🎯 **Качество:** {quality}\n"
                f"\nПолоса: **{lane}**, слоты: **{slots}**"
            )
        else:
            text = (
                f"⏳ **Вы в очереди:** #{pos or '?'}\n\n"
                f"🎬 **{title}**\n🎯 **Качество:** {quality}\n"
                f"\nПолоса: **{lane}**, слоты: **{slots}**\n"
                f"Команда: /queue — посмотреть статус"
            )
        await msg.edit_text(text)

    finally:
        active_downloads.discard(tg_user_id)
        user_video_data.pop(tg_user_id, None)
        user_audio_selections.pop(tg_user_id, None)


@router.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, bot: Bot):
    """Обработчик отмены"""
    try:
        await callback.answer("Операция отменена")

        user_id = callback.from_user.id
        if user_id in user_video_data:
            del user_video_data[user_id]
        if user_id in user_audio_selections:
            del user_audio_selections[user_id]

        if user_id in active_downloads:
            active_downloads.remove(user_id)

        video_service = getattr(bot, 'video_service', None)
        if video_service:
            await video_service.cleanup_user_files(user_id)

        keys_to_remove = []
        for key in last_progress_state.keys():
            if key[0] == callback.message.chat.id:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del last_progress_state[key]

        try:
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Error deleting message on cancel: {str(e)}")
            await callback.message.edit_text("❌ Операция отменена")
    except Exception as e:
        logger.error(f"Error in cancel handler: {str(e)}")


@router.message(Command("status"))
async def status_cmd(message: Message, bot: Bot):
    """Проверка статуса сервиса"""
    video_service = getattr(bot, 'video_service', None)
    if not video_service:
        await message.answer("❌ Сервис видео не инициализирован")
        return

    status_lines = []

    if video_service.use_proxy and video_service.proxy_url:
        status_lines.append("✅ **Прокси:** включен")
    else:
        status_lines.append("🔴 **Прокси:** не используется")

    status_lines.append(f"🎭 **Playwright:** {'доступен' if video_service.playwright_available else 'не доступен'}")
    db_pool = getattr(bot, "db_pool", None)
    if db_pool:
        try:
            from db.postgres_queue import count_running

            running_short = await count_running(db_pool, is_short=True)
            running_long = await count_running(db_pool, is_short=False)
            status_lines.append(
                f"📊 **Очередь:** short running={running_short}/{Config.QUEUE_SHORT_SLOTS}, long running={running_long}/{Config.QUEUE_GENERAL_SLOTS}"
            )
        except Exception:
            status_lines.append("📊 **Очередь:** недоступно")
    else:
        status_lines.append("📊 **Очередь:** DB не подключена")

    await message.answer("\n".join(status_lines))


@router.message(Command("queue"))
async def queue_cmd(message: Message, bot: Bot):
    """Показывает позицию пользователя в очереди."""
    db_pool = getattr(bot, "db_pool", None)
    if not db_pool:
        await message.answer("❌ PostgreSQL не подключен")
        return

    user_id = await upsert_telegram_user(
        db_pool,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        username=getattr(message.from_user, "username", None),
        first_name=getattr(message.from_user, "first_name", None),
        last_name=getattr(message.from_user, "last_name", None),
        language_code=getattr(message.from_user, "language_code", None),
    )

    job = await get_active_job_for_user(db_pool, user_id=user_id)
    if not job:
        await message.answer("ℹ️ У вас нет задач в очереди.")
        return

    title = job.title or "Видео"
    lane = "короткая" if job.is_short else "общая"

    if job.status == "queued":
        pos = await get_queue_position(db_pool, job_id=job.id)
        await message.answer(
            f"⏳ **В очереди** (полоса: {lane})\n\n🎬 **{title}**\nМесто: **#{pos or '?'}**"
        )
    else:
        await message.answer(
            f"🚀 **В работе** (полоса: {lane})\n\n🎬 **{title}**\nПрогресс: **{job.progress}%**\nСтадия: {job.stage or '...'}"
        )


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, bot: Bot):
    """Отменяет последнюю queued задачу пользователя или останавливает текущую running."""
    db_pool = getattr(bot, "db_pool", None)
    if not db_pool:
        await message.answer("❌ PostgreSQL не подключен")
        return

    user_id = await upsert_telegram_user(
        db_pool,
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        username=getattr(message.from_user, "username", None),
        first_name=getattr(message.from_user, "first_name", None),
        last_name=getattr(message.from_user, "last_name", None),
        language_code=getattr(message.from_user, "language_code", None),
    )

    job = await get_active_job_for_user(db_pool, user_id=user_id)
    if not job:
        await message.answer("ℹ️ У вас нет активных задач.")
        return

    if job.status == "queued":
        jid = await cancel_latest_queued(db_pool, user_id=user_id)
        if jid:
            await message.answer(f"✅ Отменил задачу #{jid}.")
        else:
            await message.answer("ℹ️ Нет queued задач для отмены.")
        return

    if job.status == "running":
        queue_manager = getattr(bot, "queue_manager", None)
        if not queue_manager:
            await message.answer("❌ Менеджер очереди не инициализирован.")
            return
        await queue_manager.cancel_job(job.id, reason="canceled by user")
        await message.answer(f"✅ Останавливаю задачу #{job.id}.")
        return

    await message.answer("ℹ️ Нечего отменять.")


@router.message(Command("retry"))
async def retry_cmd(message: Message, bot: Bot):
    """Повторно отправляет последний скачанный файл, если отправка в Telegram упала."""
    registry = getattr(bot, "download_registry", None)
    sender = getattr(bot, "telegram_sender", None)
    user_id = message.from_user.id

    if not registry or not sender:
        await message.answer("❌ Сервис повторной отправки не инициализирован")
        return

    if user_id in active_retry_users:
        await message.answer("⏳ Повторная отправка уже выполняется.")
        return

    cooldown_left = _remaining_cooldown(
        retry_cooldowns.get(user_id, 0.0),
        float(getattr(Config, "RETRY_USER_COOLDOWN_SECONDS", 0)),
    )
    if cooldown_left > 0:
        await message.answer(f"⏳ Повторите /retry через {cooldown_left}с.")
        return

    rec = registry.get(user_id)
    if not rec:
        await message.answer("ℹ️ Нет файла для повторной отправки. Сначала скачайте видео.")
        return

    active_retry_users.add(user_id)
    retry_cooldowns[user_id] = time.time()
    await message.answer("📤 Пробую отправить ещё раз...")

    try:
        await sender.send_media(message.chat.id, rec.file_path, rec.caption, media_kind=rec.media_kind)
        await message.answer("✅ Готово! Файл отправлен.")

        try:
            if os.path.exists(rec.file_path):
                os.remove(rec.file_path)
        except Exception:
            pass
        registry.pop(user_id)

    except TelegramEntityTooLarge:
        await message.answer(
            "❌ Telegram отклоняет файл по размеру. "
            "Если вы используете публичный Bot API — там есть жёсткий лимит; "
            "для больших файлов используйте Local Bot API Server или отдачу по ссылке с сайта."
        )
    except (TelegramNetworkError, asyncio.TimeoutError) as e:
        logger.warning(f"Retry upload failed (network): {e}")
        await message.answer("⚠️ Сеть/соединение снова оборвалось. Попробуйте /retry позже.")
    except Exception as e:
        logger.error(f"Retry upload failed: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось отправить файл. Попробуйте /retry позже.")
    finally:
        active_retry_users.discard(user_id)


@router.message(Command("cleanup"))
async def cleanup_cmd(message: Message, bot: Bot):
    """Очистка временных файлов"""
    try:
        video_service = getattr(bot, 'video_service', None)
        if video_service:
            await video_service.cleanup_user_files(message.from_user.id)

        keys_to_remove = []
        for key in last_progress_state.keys():
            if key[0] == message.chat.id:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del last_progress_state[key]

        if message.from_user.id in active_downloads:
            active_downloads.remove(message.from_user.id)
        active_analysis_users.discard(message.from_user.id)
        active_retry_users.discard(message.from_user.id)
        analysis_cooldowns.pop(message.from_user.id, None)
        retry_cooldowns.pop(message.from_user.id, None)

        registry = getattr(bot, "download_registry", None)
        if registry:
            rec = registry.pop(message.from_user.id)
            if rec and os.path.exists(rec.file_path):
                try:
                    os.remove(rec.file_path)
                except Exception as e:
                    logger.error(f"Error deleting retry file {rec.file_path}: {e}")

        await message.answer("✅ Очищены ваши локальные состояния и файл для /retry.")
    except Exception as e:
        logger.error(f"Error in cleanup: {str(e)}")
        await message.answer(f"❌ Ошибка при очистке файлов: {str(e)}")


@router.message(Command("help"))
async def help_cmd(message: Message):
    """Помощь по использованию бота"""
    help_text = """
🤖 **Помощь по использованию бота**

**Основные команды:**
/start - Начало работы
/status - Статус сервиса  
/queue - Позиция в очереди
/cancel - Отменить последнюю задачу (queued/running)
/cleanup - Очистка временных файлов
/retry - Повторно отправить последний файл (если Telegram разорвал соединение)
/help - Эта справка

**Как скачать видео:**
1. Просто отправьте ссылку на видео
2. Бот автоматически определит тип контента
3. Выберите желаемое качество
4. Дождитесь загрузки

**Для фильмов:**
• Используется расширенный поиск через Playwright
• Автоматическое определение HLS потоков
• Отслеживание прогресса загрузки
• Поддержка прокси для обхода ограничений

**Поддерживаемые платформы:**
• YouTube
• Онлайн-кинотеатры (Lordfilm, Kinopoisk, Kinogram и др.)
• Фильмы через HLS потоки

**Если возникают проблемы:**
• Попробуйте выбрать другое качество
• Проверьте интернет-соединение
• Используйте команду /cleanup для очистки временных файлов
• Если файл скачался, но не отправился (ошибка сети) — используйте /retry
• Для очень больших файлов используйте облачные хранилища
"""
    await message.answer(help_text)


@router.shutdown()
async def on_shutdown(bot: Bot):
    """Обработчик завершения работы"""
    try:
        video_service = getattr(bot, 'video_service', None)
        if video_service:
            await video_service.close()

        user_video_data.clear()
        user_audio_selections.clear()
        last_progress_state.clear()
        active_downloads.clear()
        active_analysis_users.clear()
        active_retry_users.clear()
        analysis_cooldowns.clear()
        retry_cooldowns.clear()
        logger.info("Bot shutdown completed")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")
