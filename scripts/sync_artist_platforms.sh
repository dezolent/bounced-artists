#!/bin/sh
# Railway cron entrypoint: one finite run, no web server or migrations.
set -eu
if [ "${ARTIST_SCRAPER_MODE:-hosted}" = "self_hosted" ]; then
  python manage.py check_scraper_backends --probe --streaming-only
fi
python manage.py verify_official_artist_links --apply
python manage.py configure_artist_sync --apply
exec python manage.py sync_artist_platforms --apply --request-limit "${ARTIST_SYNC_REQUEST_LIMIT:-18000}" --job-request-limit "${ARTIST_SYNC_JOB_REQUEST_LIMIT:-1000}" --time-limit "${ARTIST_SYNC_TIME_LIMIT:-7200}" --job-time-limit "${ARTIST_SYNC_JOB_TIME_LIMIT:-300}" --job-limit "${ARTIST_SYNC_JOB_LIMIT:-250}"
