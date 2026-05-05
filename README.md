# VideoFetchBot
This is a backend system with a bot and a website for parsing and downloading videos.

The main problem with systems like this is that downloading and processing videos is a long-running operation. This is bad because if this logic is executed directly inside an HTTP request, the server starts hanging and cannot properly handle other requests.

To solve this problem, the project implements a task queue, worker processes, a status system, and task reprocessing on errors.

# Architecture

The project consists of several parts:

- backend application
- Telegram bot
- worker processes
- PostgreSQL
- task queue
- task status system

As a rule, the backend accepts a request and puts a task into the queue, while the worker performs the long-running processing.

# Problems solved

- Long-running operations
This is bad because downloading a video can take time and block the server.
The solution to this problem is a task queue and separate worker processes.

- Duplicate tasks
This can lead to bad consequences, such as extra load and repeated processing.
The solution to this problem is checks and locks.

- Processing errors
For example, the network can disconnect or the service can restart.
To solve this problem, task statuses and re-queueing are used.

# Stack

Python, Django, Django REST Framework, PostgreSQL, Docker, Telegram API

# For HR

The main logic is located in the following parts:

- backend API (site/videofetch_app/views.py)
- worker logic (videofetcher/service.py)
- working with tasks and statuses (bot/queue_manager.py)
- Telegram bot (bot)
______________________________________________________________

WEBSITE
Main web interface >
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-14-18" src="screenshots/web_1.png" />

Link analysis >
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-18-09" src="screenshots/web_2.png" />

Choosing the required quality >
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-18-41" src="screenshots/web_3.png" />

Progress >
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-18-52" src="screenshots/web_4.png" />

Final screen with an offer to download video/audio >
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-19-43" src="screenshots/web_5.png" />
<img width="1365" height="660" alt="Screenshot from 2026-04-04 15-22-56" src="screenshots/web_6.png" />

Responsive interface >
<img width="389" height="660" alt="Screenshot from 2026-04-04 15-24-56" src="screenshots/web_7.png" />
______________________________________________________________
TELEGRAM BOT
Start >
<img width="933" height="1280" alt="photo_2026-04-04_20-35-54" src="screenshots/tg_1.jpg" />

Quality selection >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-55" src="screenshots/tg_2.jpg" />

Ready video >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-56" src="screenshots/tg_3.jpg" />

Movie processing >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-57" src="screenshots/tg_4.jpg" />

Voice-over selection >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-58" src="screenshots/tg_5.jpg" />

Progress >
<img width="578" height="1280" alt="photo_2026-04-04_20-35-59" src="screenshots/tg_6.jpg" />

Ready video (there is no preview, this is a Telegram feature) >
<img width="578" height="1280" alt="photo_2026-04-04_20-36-00" src="screenshots/tg_7.jpg" />
