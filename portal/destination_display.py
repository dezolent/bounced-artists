"""Choose distinct recording destinations without altering source evidence."""
import re
from urllib.parse import unquote, urlsplit

from catalog.reconciliation import platform_identity_keys, title_key
from .recording_metrics import canonical_url, locator


def visible_recording_destinations(objects, title):
    seen = set()
    for obj in objects:
        slug, kind, external_id, url = locator(obj)
        if slug in {'spotify', 'apple-music'} and kind != 'track':
            continue
        if slug == 'tiktok' and kind == 'music':
            path = unquote(urlsplit(url).path).strip('/').split('/')
            sound_name = path[-1].rsplit('-', 1)[0] if len(path) == 2 and path[0] == 'music' else ''
            if not sound_name or title_key(title) not in title_key(sound_name):
                continue
        keys = platform_identity_keys(obj)
        if url and slug != 'apple-music':
            keys.add(('url', slug, canonical_url(url)))
        if keys & seen:
            continue
        seen.update(keys)
        yield obj
