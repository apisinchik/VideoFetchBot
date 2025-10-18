from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging

logger = logging.getLogger(__name__)


def create_quality_keyboard(formats):
    """Создаем клавиатуру для выбора качества с двумя столбцами"""
    builder = InlineKeyboardBuilder()

    # Добавляем кнопки с форматами
    for i, fmt in enumerate(formats):
        callback_data = f"quality:{i}"

        # Создаем текст кнопки с эмодзи
        if 'Аудио' in fmt['quality']:
            button_text = "🎵 Аудио"
        else:
            # Упрощаем текст - только разрешение
            height = fmt.get('height', 0)
            if height:
                button_text = f"🎬 {height}p"
            else:
                button_text = "🎬 Видео"

        # Добавляем размер файла если известен
        if fmt['filesize'] != "~":
            button_text += f" ({fmt['filesize']})"

        builder.button(
            text=button_text,
            callback_data=callback_data
        )

    # Добавляем кнопку отмены
    builder.button(text="❌ Отмена", callback_data="cancel")

    # Настраиваем расположение: все кнопки форматов в 2 столбца, отмена отдельно
    # Правильная настройка для двух столбцов
    if len(formats) > 0:
        # Вычисляем количество строк для форматов
        rows_for_formats = (len(formats) + 1) // 2  # Округление вверх

        # Создаем список настроек: 2 столбца для каждой строки форматов, затем 1 для отмены
        adjust_pattern = [2] * rows_for_formats + [1]
        builder.adjust(*adjust_pattern)
    else:
        builder.adjust(1)  # Только кнопка отмены

    return builder.as_markup()