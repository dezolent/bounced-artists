#!/bin/sh
set -eu
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --timeout 30 --access-logfile - --error-logfile - --forwarded-allow-ips '*'
