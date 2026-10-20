import os
from .base import *  # noqa: F403
from .environment import database_config
from django.core.exceptions import ImproperlyConfigured

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or "local-development-only-never-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
DATABASES = {"default": database_config(os.environ.get("DATABASE_URL", "postgresql://bounced_dev:bounced_dev@127.0.0.1:55432/bounced_artists_dev"))}
if DATABASES["default"]["HOST"] not in {"localhost", "127.0.0.1", "::1"}:
    raise ImproperlyConfigured("Local development requires a separate loopback PostgreSQL database.")
if {"host", "hostaddr", "service", "servicefile"} & DATABASES["default"]["OPTIONS"].keys() or any(
    os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
):
    raise ImproperlyConfigured("Local loopback database destination overrides are not allowed.")
