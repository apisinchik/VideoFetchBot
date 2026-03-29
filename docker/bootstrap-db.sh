#!/bin/sh
set -eu

cd /app

# Сначала поднимаем встроенные Django-таблицы, от которых зависит schema.sql:
# auth_user, django_session, django_admin_log и т.д.
python site/manage.py migrate contenttypes --noinput
python site/manage.py migrate auth --noinput
python site/manage.py migrate admin --noinput
python site/manage.py migrate sessions --noinput

python - <<'PY'
import asyncio
import pathlib
import sys

ROOT = pathlib.Path("/app")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config
from db.postgres_db import create_pool, init_schema


async def main():
    pool = await create_pool(Config.POSTGRES_DSN, min_size=1, max_size=2)
    try:
        await init_schema(pool, ROOT / "db" / "schema.sql")
    finally:
        await pool.close()


asyncio.run(main())
PY

python site/manage.py migrate videofetch_app 0003_delete_message --fake --noinput

python site/manage.py migrate --noinput
