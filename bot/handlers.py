from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot
import os
import logging
import asyncio

router = Router()
logger = logging.getLogger(__name__)

# Временное хранилище для форматов видео
user_video_data = {}


@router.message(Command("start"))
async def start_cmd(message: Message):
    welcome_text = """
🎬 **Добро пожаловать в Video Downloader Bot!**

📋 **Как использовать:**
1. Отправьте ссылку на видео с YouTube
2. Выберите желаемое качество  
3. Получите видео!

🎵 **Доступно:**
• Видео с аудио (разные качества)
• Только аудио (MP3)

🚀 **Особенности:**
• Работает через SOCKS прокси
• Быстрое скачивание
• Автоматический выбор лучшего качества

💡 **Поддерживаемые платформы:** YouTube
"""
    await message.answer(welcome_text)


@router.message(Command("proxy_status"))
async def proxy_status_cmd(message: Message, bot: Bot):
    """Проверка статуса SOCKS прокси"""
    video_service = getattr(bot, 'video_service', None)
    if not video_service:
        await message.answer("❌ Сервис видео не доступен")
        return

    if video_service.proxy_url and video_service.use_proxy:
        status_msg = f"✅ SOCKS прокси настроен:\n`{video_service.proxy_url}`\n\n"
        status_msg += "Порт: 10810\n"
        status_msg += "Хост: 127.0.0.1 (localhost)"
    else:
        status_msg = "❌ SOCKS прокси не настроен, используется прямое соединение"

    await message.answer(status_msg)


@router.message(F.text)
async def handle_text(message: Message, bot: Bot):
    text = message.text.strip()

    if not text.startswith(('http://', 'https://')):
        await message.answer("❌ Пожалуйста, отправьте корректную ссылку на видео")
        return

    progress_msg = await message.answer("🔍 Анализирую ссылку...")

    try:
        video_service = getattr(bot, 'video_service', None)
        if not video_service:
            await progress_msg.edit_text("❌ Сервис видео не доступен. Попробуйте позже.")
            return

        # Получаем информацию о видео и доступные форматы
        video_info, formats, status = await video_service.extract_video_info(text)

        if status == "success" and video_info and formats:
            # Сохраняем форматы во временное хранилище
            user_video_data[message.from_user.id] = {
                'formats': formats,
                'video_info': video_info,
                'original_url': text
            }

            # Формируем сообщение с превью
            duration_text = video_service._format_duration(video_info.get('duration', 0))
            uploader_text = f"👤 {video_info.get('uploader', 'Unknown')}"
            platform_text = f"🌐 {video_info.get('extractor', 'Unknown platform')}"

            # Для фильмов показываем специальное сообщение
            if video_info.get('extractor') == 'html_parser':
                platform_text = "🎬 Фильм"

            preview_text = f"""
🎬 **{video_info.get('title', 'Видео')}**

{uploader_text}
{platform_text}
⏱️ **Длительность:** {duration_text}
🎯 **Доступно форматов:** {len(formats)}

👇 **Выберите качество для скачивания:**
"""
            try:
                from keyboards import create_quality_keyboard
                await progress_msg.edit_text(
                    preview_text,
                    reply_markup=create_quality_keyboard(formats)
                )
            except TelegramBadRequest as e:
                logger.error(f"TelegramBadRequest: {str(e)}")
                from keyboards import create_quality_keyboard
                await message.answer(
                    preview_text,
                    reply_markup=create_quality_keyboard(formats)
                )

        else:
            error_messages = {
                "video_info_error": "❌ Не удалось получить информацию о видео",
                "no_formats_found": "❌ Не удалось найти доступные форматы видео",
                "unsupported_url": "❌ Данный тип ссылки не поддерживается",
                "invalid_url": "❌ Неверный формат ссылки",
                "failed_to_fetch_html": "❌ Не удалось загрузить страницу",
                "no_video_found_in_html": "❌ Не удалось найти видео на странице",
                "html_extraction_error": "❌ Ошибка при анализе страницы",
                "private_video": "🔒 Видео является приватным",
                "video_unavailable": "❌ Видео недоступно",
            }

            base_status = status.split(":")[0] if ":" in status else status
            error_msg = error_messages.get(base_status, f"❌ Ошибка: {status}")

            try:
                await progress_msg.edit_text(error_msg)
            except TelegramBadRequest:
                await message.answer(error_msg)

    except Exception as e:
        logger.error(f"Unexpected error in handle_text: {str(e)}", exc_info=True)
        try:
            await progress_msg.edit_text(f"❌ Произошла непредвиденная ошибка: {str(e)}")
        except TelegramBadRequest:
            await message.answer(f"❌ Произошла непредвиденная ошибка: {str(e)}")


@router.callback_query(F.data.startswith("quality:"))
async def handle_quality_selection(callback: CallbackQuery, bot: Bot):
    """Обработчик выбора качества с удалением сообщения после отправки"""
    try:
        # Немедленно отвечаем на callback чтобы избежать таймаута
        await callback.answer()

        # Сохраняем ссылку на сообщение с выбором качества для последующего удаления
        quality_message = callback.message

        # Парсим callback data
        data_parts = callback.data.split(":")
        if len(data_parts) < 2:
            await callback.message.answer("❌ Неверный формат данных")
            return

        quality_index = data_parts[1]

        # Получаем сохраненные данные
        user_data = user_video_data.get(callback.from_user.id)
        if not user_data:
            await callback.message.answer("❌ Данные устарели. Отправьте ссылку снова.")
            return

        formats = user_data['formats']
        video_info = user_data['video_info']
        original_url = user_data['original_url']

        # Находим выбранный формат по индексу
        try:
            quality_index = int(quality_index)
            if quality_index < 0 or quality_index >= len(formats):
                await callback.message.answer("❌ Неверный выбор качества")
                return

            selected_format = formats[quality_index]
        except (ValueError, IndexError):
            await callback.message.answer("❌ Неверный выбор качества")
            return

        # Сразу обновляем сообщение о начале скачивания
        download_msg = await callback.message.answer(
            f"⏳ **Скачиваю...**\n\n"
            f"🎬 **{video_info.get('title', 'Видео')}**\n"
            f"🎯 **Качество:** {selected_format['quality']}\n"
            f"💾 **Размер:** {selected_format['filesize']}\n"
            f"⏱️ **Примерное время:** 1-3 минуты\n\n"
            f"*Используется SOCKS прокси...*"
        )

        # Получаем video_service и скачиваем в фоне
        video_service = getattr(bot, 'video_service', None)
        if not video_service:
            await download_msg.edit_text("❌ Сервис видео не доступен")
            return

        # Скачиваем видео в отдельной задаче
        try:
            file_path = await asyncio.wait_for(
                video_service.download_video(selected_format, callback.from_user.id),
                timeout=300  # 5 минут таймаут
            )

            # Удаляем сообщение с выбором качества
            try:
                await quality_message.delete()
            except Exception as e:
                logger.error(f"Error deleting quality message: {str(e)}")

            # Определяем тип контента для отправки
            is_audio = 'Аудио' in selected_format['quality']

            if is_audio:
                # Отправляем аудио используя FSInputFile
                audio_file = FSInputFile(file_path)
                await callback.message.answer_audio(
                    audio=audio_file,
                    caption=f"🎵 **{video_info.get('title', 'Аудио')}**\n\n"
                            f"✅ **Формат:** MP3\n"
                            f"💾 **Размер:** {selected_format['filesize']}\n"
                            f"📦 **Успешно загружено!**",
                    title=video_info.get('title', 'Аудио')[:64]  # Ограничение Telegram
                )
            else:
                # Отправляем видео используя FSInputFile
                video_file = FSInputFile(file_path)
                await callback.message.answer_video(
                    video=video_file,
                    caption=f"🎬 **{video_info.get('title', 'Видео')}**\n\n"
                            f"✅ **Качество:** {selected_format['quality']}\n"
                            f"💾 **Размер:** {selected_format['filesize']}\n"
                            f"📦 **Успешно загружено!**",
                    supports_streaming=True
                )

            # Удаляем временный файл
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.error(f"Error deleting temp file: {str(e)}")

            # Удаляем сообщение о скачивании
            try:
                await download_msg.delete()
            except:
                pass

            # Очищаем данные пользователя
            if callback.from_user.id in user_video_data:
                del user_video_data[callback.from_user.id]

        except asyncio.TimeoutError:
            await download_msg.edit_text("❌ Таймаут при скачивании. Попробуйте позже.")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Download error: {error_msg}", exc_info=True)

            error_text = ""
            if "too large" in error_msg.lower():
                error_text = "❌ Файл слишком большой для Telegram"
            elif any(err in error_msg for err in ['timeout', 'SSL', 'SOCKS', 'proxy']):
                error_text = (
                    "❌ Проблема с SOCKS прокси или сетью\n\n"
                    "Проверьте:\n"
                    "• Запущен ли SOCKS прокси на 127.0.0.1:10810\n"
                    "• Настройки брандмауэра\n"
                    "• Попробуйте позже или используйте другое качество"
                )
            elif "unavailable" in error_msg.lower():
                error_text = "❌ Видео недоступно или удалено"
            else:
                error_text = f"❌ Ошибка загрузки: {error_msg}"

            await download_msg.edit_text(error_text)

    except Exception as e:
        logger.error(f"Error in quality selection: {str(e)}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при обработке запроса")


@router.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, bot: Bot):
    """Обработчик отмены с удалением сообщения"""
    # Немедленно отвечаем на callback
    await callback.answer()

    # Очищаем данные пользователя
    if callback.from_user.id in user_video_data:
        del user_video_data[callback.from_user.id]

    # Очищаем временные файлы
    video_service = getattr(bot, 'video_service', None)
    if video_service:
        await video_service.cleanup_user_files(callback.from_user.id)

    # Удаляем сообщение с выбором качества
    try:
        await callback.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message on cancel: {str(e)}")
        try:
            await callback.message.edit_text("❌ Операция отменена")
        except:
            await callback.message.answer("❌ Операция отменена")


@router.message(Command("status"))
async def status_cmd(message: Message, bot: Bot):
    """Проверка статуса сервиса"""
    video_service = getattr(bot, 'video_service', None)
    if not video_service:
        await message.answer("❌ Сервис видео не инициализирован")
        return

    status_lines = []

    if video_service.use_proxy and video_service.proxy_url:
        status_lines.append(f"✅ **Прокси:** {video_service.proxy_url}")
    else:
        status_lines.append("🔴 **Прокси:** не используется")

    status_lines.append(f"📁 **Временная директория:** {video_service.temp_dir}")

    await message.answer("\n".join(status_lines))


@router.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, bot: Bot):
    """Обработчик отмены с удалением сообщения"""
    # Немедленно отвечаем на callback
    await callback.answer()

    # Очищаем данные пользователя
    if callback.from_user.id in user_video_data:
        del user_video_data[callback.from_user.id]

    # Очищаем временные файлы
    video_service = getattr(bot, 'video_service', None)
    if video_service:
        await video_service.cleanup_user_files(callback.from_user.id)

    # Удаляем сообщение с выбором качества
    try:
        await callback.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message on cancel: {str(e)}")
        try:
            await callback.message.edit_text("❌ Операция отменена")
        except:
            await callback.message.answer("❌ Операция отменена")


@router.message(Command("cleanup"))
async def cleanup_cmd(message: Message, bot: Bot):
    """Очистка временных файлов"""
    try:
        video_service = getattr(bot, 'video_service', None)
        if video_service:
            await video_service.cleanup_user_files(message.from_user.id)

        cleaned_count = 0
        if os.path.exists('temp'):
            for file in os.listdir('temp'):
                file_path = os.path.join('temp', file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    cleaned_count += 1
            await message.answer(f"✅ Очищено {cleaned_count} временных файлов!")
        else:
            await message.answer("✅ Временная директория уже чиста!")
    except Exception as e:
        await message.answer(f"❌ Ошибка при очистке файлов: {str(e)}")


@router.shutdown()
async def on_shutdown(bot: Bot):
    """Обработчик завершения работы"""
    video_service = getattr(bot, 'video_service', None)
    if video_service:
        await video_service.close()

    # Очищаем данные
    user_video_data.clear()