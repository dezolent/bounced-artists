#!/bin/sh
set -eu
python manage.py check --deploy --fail-level WARNING
python manage.py migrate --noinput
python manage.py seed_artists
