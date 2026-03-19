import asyncio
import time

async def tg_progress(start_time: float, audio_name, title, quality, prog_int, chat_id, msg_id):
    elapsed = int(time.time() - start_time)
    text = f"⏳ **Скачивание...**\n\n🎬 **{title}**\n🎯 **Качество:** {quality}\n"
    if audio_name:
        text += f"🎵 **Озвучка:** {audio_name}\n"
    text += f"\n📥 Прогресс: **{prog_int}%**\n⏱ Прошло: {elapsed}s"
    await bot.edit_message_text(text=text, chat_id=chat_id, message_id=msg_id)