from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.core.exceptions import ValidationError


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Use a valid IANA timezone, such as America/New_York.") from None
