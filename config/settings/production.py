from .base import *  # noqa: F403
from .environment import allowed_hosts, database_config, required, trusted_origins
from django.core.exceptions import ImproperlyConfigured

SECRET_KEY = required("DJANGO_SECRET_KEY")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a strong random value of at least 50 characters.")
DATABASES = {"default": database_config(required("DATABASE_URL"))}
ALLOWED_HOSTS = allowed_hosts(required("DJANGO_ALLOWED_HOSTS"))
CSRF_TRUSTED_ORIGINS = trusted_origins(required("DJANGO_CSRF_TRUSTED_ORIGINS"))
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# Railway probes use internal HTTP. Only these paths are exempt from redirect.
SECURE_REDIRECT_EXEMPT = [r"^health/$", r"^ready/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
# This service does not control every bounced.studio subdomain.
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_REFERRER_POLICY = "same-origin"
# Deliberate: do not force HTTPS/preload policy onto unrelated subdomains.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
