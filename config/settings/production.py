from .base import *  # noqa: F403
from .environment import allowed_hosts, database_config, required, trusted_origins
from django.core.exceptions import ImproperlyConfigured
import os

SECRET_KEY = required("DJANGO_SECRET_KEY")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a strong random value of at least 50 characters.")
DATABASES = {"default": database_config(required("DATABASE_URL"))}
ALLOWED_HOSTS = allowed_hosts(required("DJANGO_ALLOWED_HOSTS"))
CSRF_TRUSTED_ORIGINS = trusted_origins(required("DJANGO_CSRF_TRUSTED_ORIGINS"))
# This application hostname was explicitly authorized; preserve configured hosts.
if 'artists.bounced.studio' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('artists.bounced.studio')
if 'https://artists.bounced.studio' not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append('https://artists.bounced.studio')

# Never write uploads to ephemeral production container storage.
_media_names = ('R2_ACCOUNT_ID','R2_BUCKET_NAME','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY')
_media_present = [bool(os.environ.get(name)) for name in _media_names]
if any(_media_present) and not all(_media_present):
    raise ImproperlyConfigured('Configure all R2 storage variables together.')
PORTAL_UPLOADS_ENABLED = all(_media_present)
if PORTAL_UPLOADS_ENABLED:
    import re
    if not re.fullmatch(r'[a-f0-9]{32}',os.environ['R2_ACCOUNT_ID']):
        raise ImproperlyConfigured('R2_ACCOUNT_ID must be a Cloudflare account identifier.')
    STORAGES = dict(STORAGES)
    STORAGES['default'] = {'BACKEND':'storages.backends.s3.S3Storage','OPTIONS':{
        'access_key':os.environ['R2_ACCESS_KEY_ID'],
        'secret_key':os.environ['R2_SECRET_ACCESS_KEY'],
        'bucket_name':os.environ['R2_BUCKET_NAME'],
        'endpoint_url':f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        'region_name':'auto','signature_version':'s3v4','default_acl':None,
        'file_overwrite':False,'querystring_auth':True,
        'object_parameters':{'CacheControl':'private, no-store'},
    }}
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
