"""Only for collectstatic in Docker; no production secrets or database access."""
from .base import *  # noqa: F403
SECRET_KEY = "static-collection-build-only"
ALLOWED_HOSTS = []
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
