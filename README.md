# VideoFetchBot
Это backend система бот + сайт с парсингом и скачиванием видео.

Основная проблема таких систем в том, что скачивание и обработка видео является долгой операцией. Это плохо, так как если выполнять такую логику прямо в HTTP-запросе, сервер начинает подвисать и не может нормально обрабатывать другие запросы.

Чтобы решить эту проблему, в проекте реализована очередь задач, worker процессы, система статусов и повторная обработка задач при ошибках.

# Архитектура

Проект состоит из нескольких частей:

- backend приложение
- Telegram бот
- worker процессы
- PostgreSQL
- очередь задач
- система статусов задач

Как правило, backend принимает запрос и ставит задачу в очередь, а worker уже выполняет долгую обработку.

# Какие проблемы решены

- Долгие операции
Это плохо, так как скачивание видео может занимать время и блокировать сервер.
Решением данной проблемы является очередь задач и отдельные worker процессы.

- Дубли задач
Это может привести к плохим последствиям, например к лишней нагрузке и повторной обработке.
Решением данной проблемы являются проверки и блокировки.

- Ошибки при обработке
Например, сеть может оборваться или сервис может перезапуститься.
Чтобы решить эту проблему, используются статусы задач и повторная постановка в очередь.

# Стек

Python, Django, Django REST Framework, PostgreSQL, Docker, Telegram API

# Для HR

Основная логика находится в следующих частях:

- backend API (site/videofetch_app/views.py)
- worker логика (videofetcher/service.py)
- работа с задачами и статусами (bot/queue_manager.py)
- Telegram бот (bot)
______________________________________________________________

ВЕБ-САЙТ
Главный веб-интерфейс >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-14-18" src="https://github.com/user-attachments/assets/c591c0e4-2590-4186-a0e9-c9faeff92f96" />

Анализ ссылки >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-09" src="https://github.com/user-attachments/assets/38290fbb-0202-4579-a06a-e71d71eb89ff" />

Выбор нужного качества >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-41" src="https://github.com/user-attachments/assets/f001d3b5-145b-4c46-a82d-2e0aef435623" />

Прогресс >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-52" src="https://github.com/user-attachments/assets/a0634332-dffb-44f0-9e81-00e1eddb08b0" />

Готовый экран с предложением скачать видео/аудио >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-19-43" src="https://github.com/user-attachments/assets/9a837b1c-2e5b-413b-83fd-33fc5882d8d5" />
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-22-56" src="https://github.com/user-attachments/assets/0060089c-a1d4-4c92-98e5-64b31d18ea5f" />

Адаптивный интерфейс >
<img width="389" height="660" alt="Снимок экрана от 2026-04-04 15-24-56" src="https://github.com/user-attachments/assets/a8ecd4c0-7683-4c26-8a67-d8434fa13d6a" />
______________________________________________________________
ТЕЛЕГРАМ БОТ
Старт > 
<img width="933" height="1280" alt="photo_2026-04-04_20-35-54" src="https://github.com/user-attachments/assets/3982f1a1-82e3-4090-a33a-fb0b83e6f274" />

Выбор качества >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-55" src="https://github.com/user-attachments/assets/e846e153-c52c-4617-af5d-9a2ec00e9119" />

Готовое видео >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-56" src="https://github.com/user-attachments/assets/10e70100-c645-4cf9-98ae-d39c83404cd4" />

Обработка фильма >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-57" src="https://github.com/user-attachments/assets/cc8df165-f58c-4bf7-b853-7622ff44f07b" />

Выбор озвучки >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-58" src="https://github.com/user-attachments/assets/2b1e3761-5865-4d12-8b87-6e61a1e213d4" />

Прогресс >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-59" src="https://github.com/user-attachments/assets/6f673521-7df8-4c1d-9ba9-c0839ecc8211" />

Готовое видео (превью нет, особенность телеграма) >
<img width="578" height="1280" alt="photo_2026-04-04_20-36-00" src="https://github.com/user-attachments/assets/2498e7d4-858f-4755-8482-7c59c70092ca" />

