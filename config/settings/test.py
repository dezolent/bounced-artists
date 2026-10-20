from .local import *  # noqa: F403
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "healthcheck.railway.app"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
