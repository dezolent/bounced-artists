from django.core.exceptions import ValidationError

SECRET_KEYS = {"password", "passwd", "authorization", "cookie", "secret", "client_secret", "api_key", "access_token", "refresh_token", "private_key"}


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace("-", "_") in SECRET_KEYS:
                raise ValidationError("Sensitive credential fields cannot be stored in source or audit payloads.")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)
