#!/bin/sh
# Railway cron entrypoint: one finite run, no web server or migrations.
set -eu
python manage.py verify_official_artist_links --apply
python manage.py configure_artist_sync --apply
exec python manage.py sync_artist_platforms --apply --request-limit "${ARTIST_SYNC_REQUEST_LIMIT:-15000}" --job-request-limit "${ARTIST_SYNC_JOB_REQUEST_LIMIT:-1000}" --job-limit "${ARTIST_SYNC_JOB_LIMIT:-250}"
