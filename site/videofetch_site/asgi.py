"""ASGI config for videofetch_site project."""

import os
import asyncio
import pathlib
import sys
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from videofetcher.initialize import init_videofetcher

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'videofetch_site.settings')

django_app = get_asgi_application()
if settings.DEBUG:
    django_app = ASGIStaticFilesHandler(django_app)


async def application(scope, receive, send):
    if scope['type'] == 'lifespan':
        while True:
            message = await receive()

            if message['type'] == 'lifespan.startup':
                try:
                    await init_videofetcher()
                except Exception as e:
                    await send(
                        {'type': 'lifespan.startup.failed', 'message': str(e)}
                    )
                    return
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                await send({'type': 'lifespan.shutdown.complete'})
                return
    else:
        await django_app(scope, receive, send)
