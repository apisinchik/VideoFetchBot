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
<img width="217" height="403" alt="image" src="https://github.com/user-attachments/assets/8242881f-96e3-4719-b93c-9e1e3dcb370d" />
______________________________________________________________


ВЕБ-САЙТ
Главный веб-интерфейс >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-14-18" src="https://github.com/user-attachments/assets/8a17df48-2f80-4c6a-b7b1-97b63ba9eb11" />

Анализ ссылки >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-09" src="https://github.com/user-attachments/assets/e02c946b-6511-4aa1-80c9-ba2e544177bd" />

Выбор нужного качества >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-41" src="https://github.com/user-attachments/assets/9b2f467d-8db2-4bb5-9e7e-e2d882bcb248" />

Прогресс >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-18-52" src="https://github.com/user-attachments/assets/e1928cdb-a71a-4747-9a91-cdbbfcbb4082" />

Готовый экран с предложением скачать видео/аудио >
<img width="1365" height="660" alt="Снимок экрана от 2026-04-04 15-19-43" src="https://github.com/user-attachments/assets/fd8fb7a6-e1ce-4a4e-8a82-50335702d547" />

Адаптивный интерфейс >
<img width="389" height="660" alt="Снимок экрана от 2026-04-04 15-24-56" src="https://github.com/user-attachments/assets/70d1a896-87a8-4897-9db8-2f004794e1f1" />
______________________________________________________________
ТЕЛЕГРАМ БОТ
Старт > 
![photo_2026-04-04_20-35-54](https://github.com/user-attachments/assets/6ebcb632-a41d-48dd-87db-f1faabda5ffc)

Выбор качества >
![photo_2026-04-04_20-35-55](https://github.com/user-attachments/assets/7bb2649c-9e65-4936-8509-a2ce5042cbeb)

Готовое видео >
![photo_2026-04-04_20-35-56](https://github.com/user-attachments/assets/a97088ee-c9b6-4b69-b225-84a1b94a2c07)

Обработка фильма >
![photo_2026-04-04_20-35-57](https://github.com/user-attachments/assets/5ccc3d39-d2d0-4f17-9f0f-054a3c4b9d25)

Выбор озвучки >
![photo_2026-04-04_20-35-58](https://github.com/user-attachments/assets/6bde039c-4c1c-4b1e-b738-bd70c6ea91d5)

Прогресс >
![photo_2026-04-04_20-35-59](https://github.com/user-attachments/assets/707f5f27-c26d-43ea-bc8e-0b4b9350eaa4)

Готовое видео (превью нет, особенность телеграма) >
![photo_2026-04-04_20-36-00](https://github.com/user-attachments/assets/cc8c81b0-272f-4dfa-ab51-5b812aeb81ed)

