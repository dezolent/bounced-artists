import os
import re
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is required.")
    return value


def database_config(url):
    # Never include a malformed URL (possibly containing credentials) in errors.
    try:
        parsed = dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True)
        if parsed["ENGINE"] != "django.db.backends.postgresql" or not parsed.get("NAME") or not parsed.get("HOST"):
            raise ValueError
    except Exception:
        raise ImproperlyConfigured("DATABASE_URL must be a valid PostgreSQL URL with host and database.") from None
    parsed.setdefault("OPTIONS", {})["connect_timeout"] = 5
    return parsed


def allowed_hosts(value):
    hosts = [host.strip() for host in value.split(",") if host.strip()]
    if not hosts or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host) for host in hosts):
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must contain explicit hostnames without wildcards.")
    return hosts


def trusted_origins(value):
    origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    try:
        for origin in origins:
            parts = urlsplit(origin)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
                raise ValueError
            allowed_hosts(parts.hostname)
            _ = parts.port
        if not origins:
            raise ValueError
    except (ValueError, ImproperlyConfigured):
        raise ImproperlyConfigured("DJANGO_CSRF_TRUSTED_ORIGINS requires explicit HTTPS origins.") from None
    return origins
