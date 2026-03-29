#!/bin/sh
set -eu

interval="${CLEANUP_INTERVAL_SECONDS:-900}"

while true; do
    python site/manage.py cleanup_web_jobs
    sleep "${interval}"
done
