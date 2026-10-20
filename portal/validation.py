import ipaddress
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError


def safe_external_url(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        port = parsed.port
    except ValueError:
        raise ValidationError('Enter a valid HTTPS URL.')
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or port not in (None,443):
        raise ValidationError('Use a public HTTPS URL without credentials or a custom port.')
    if host == 'localhost' or '.' not in host or host.endswith(('.localhost','.local','.internal')):
        raise ValidationError('Private network addresses are not permitted.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValidationError('Private network addresses are not permitted.')
    if any(key in parsed.query.lower() for key in ('token=', 'secret=', 'password=', 'signature=', 'x-amz-')):
        raise ValidationError('Remove credentials and signed parameters from this URL.')
    return value
