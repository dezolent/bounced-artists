#!/bin/sh
set -eu
python manage.py check --deploy --fail-level WARNING
python manage.py migrate --noinput
python manage.py seed_artists
python manage.py seed_references
python manage.py verify_official_artist_links --apply
