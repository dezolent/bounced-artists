"""Trusted deployment configuration, separate from scraped URLs and credentials."""
from dataclasses import dataclass
import hashlib
import ipaddress
import os
import re
from urllib.parse import urlsplit
from .http import CollectionError

SCRAPER_PLATFORMS = frozenset({'spotify','apple-music','youtube','soundcloud','tiktok','instagram'})


@dataclass(frozen=True)
class ScraperBackend:
    mode: str
    origin: str
    revision: str = ""

    @property
    def identity(self):
        return hashlib.sha256(f'{self.mode}\0{self.origin}\0{self.revision}'.encode()).hexdigest()


def scraper_mode():
    mode = os.environ.get('ARTIST_SCRAPER_MODE','hosted').strip().lower()
    if mode not in {'hosted','self_hosted'}:
        raise CollectionError('invalid_scraper_backend')
    return mode


def scraper_backend(platform):
    if platform == "spotify-audio-features":
        return ScraperBackend("rapidapi", "https://spotify-extended-audio-features-api.p.rapidapi.com")
    if platform not in SCRAPER_PLATFORMS:
        raise CollectionError('unsupported_platform')
    mode = scraper_mode()
    if mode == 'hosted':
        return ScraperBackend(mode,f'https://{platform}-scraper.omkar.cloud')
    key = 'ARTIST_SCRAPER_' + platform.replace('-','_').upper() + '_URL'
    origin = os.environ.get(key,'').strip()
    if not origin:
        raise CollectionError('missing_scraper_url')
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ''
        if (parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password
                or parsed.path not in {'','/'} or any(c in origin for c in ('?','#','\\'))
                or any(c.isspace() for c in origin) or parsed.port == 0):
            raise ValueError
        # HTTP is limited to the local runtime or a Railway private service.
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost'
        if not (loopback or re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?',host)):
            raise ValueError
        if parsed.scheme == 'http' and not (loopback or host.endswith('.railway.internal')):
            raise ValueError
    except (ValueError,TypeError,UnicodeError):
        raise CollectionError('invalid_scraper_url') from None
    revision = os.environ.get(key.removesuffix('_URL') + '_REVISION','').strip()
    if revision and not re.fullmatch(r'[a-fA-F0-9]{40}',revision):
        raise CollectionError('invalid_scraper_backend')
    return ScraperBackend(mode,origin.rstrip('/'),revision.lower())


def catalog_enrichment_enabled():
    value = os.environ.get('ARTIST_SYNC_CATALOG_ENRICHMENT')
    if value is None:
        return scraper_mode() == 'self_hosted'
    if value.strip().lower() not in {'0','1','false','true'}:
        raise CollectionError('invalid_scraper_backend')
    return value.strip().lower() in {'1','true'}
