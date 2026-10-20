from .local import *  # noqa: F403
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "healthcheck.railway.app"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# A website bundle contains Unicode lyrics and article bodies. PostgreSQL test
# databases must be UTF-8 even when a developer's template1 is SQL_ASCII.
DATABASES["default"].setdefault("TEST", {}).update({"CHARSET": "UTF8", "TEMPLATE": "template0"})
