"""Small compatibility boundary around the pinned standalone scraper routes.

Keep upstream source intact. Reuse shared enrichment lookups and preserve its
exception taxonomy without exposing exception text or retrying login gates.
"""
from collections import OrderedDict
from copy import deepcopy
from functools import wraps
import importlib
import json
import threading
import time


class memoize:
    def __init__(self, fn, *, maxsize=128, ttl=300):
        self.fn, self.maxsize, self.ttl = fn, maxsize, ttl
        self.entries = OrderedDict()
        self.lock = threading.RLock()

    def __call__(self, *args, **kwargs):
        key = json.dumps([args, kwargs], sort_keys=True)
        # The private server has four threads. Serialize shared misses as well
        # as hits so simultaneous enrichment calls cannot duplicate a fetch.
        with self.lock:
            now = time.monotonic()
            entry = self.entries.get(key)
            if entry and now < entry[0]:
                self.entries.move_to_end(key)
                return deepcopy(entry[1])
            self.entries.pop(key, None)
            result = self.fn(*args, **kwargs)
            self.remember(result, *args, **kwargs)
            return deepcopy(result)

    def remember(self, result, *args, **kwargs):
        with self.lock:
            key = json.dumps([args, kwargs], sort_keys=True)
            self.entries[key] = (time.monotonic() + self.ttl, deepcopy(result))
            self.entries.move_to_end(key)
            while len(self.entries) > self.maxsize:
                self.entries.popitem(last=False)


def cache_musixmatch(lookup):
    original = lookup.track_object

    @wraps(original)
    def fetch(ref):
        result = original(ref)
        native_id = result.get('track_id')
        if isinstance(native_id, int) and not isinstance(native_id, bool) and native_id > 0:
            # Details resolve a Spotify ref; lyrics/writers subsequently request
            # this exact native ID. Avoid paying for track.get a second time.
            cached.remember(result, {'kind': 'track_id', 'value': native_id})
        return result

    cached = memoize(fetch)
    lookup.track_object = cached


def install(platform, routes):
    if platform not in {'instagram', 'shazam', 'musixmatch', 'tiktok'}:
        return
    transport = importlib.import_module(platform + '.fetch')
    prefix = {'instagram': 'Instagram', 'shazam': 'Shazam',
              'musixmatch': 'Musixmatch', 'tiktok': 'TikTok'}[platform]

    def classify(exc):
        for suffix, code in [('Gated', 'provider_login_required'),
                             ('Blocked', 'provider_blocked'),
                             ('UpstreamError', 'provider_upstream_error')]:
            cls = getattr(transport, prefix + suffix, None)
            if cls is not None and isinstance(exc, cls):
                return code
        return 'provider_internal_error'

    original = routes.call

    @wraps(original)
    def call(*args, **kwargs):
        code = None
        arguments = list(args)
        index = 1 if platform == 'instagram' else 2
        fn = arguments[index]

        def guarded(*a, **kw):
            nonlocal code
            try:
                return fn(*a, **kw)
            except Exception as exc:
                code = classify(exc)
                raise

        arguments[index] = guarded
        result = original(*arguments, **kwargs)
        status = routes.response.status_code
        if status >= 400:
            # Retain validation/not-found status codes. Never return raw upstream
            # exception strings, which can contain proxy credentials or tokens.
            code = code if status >= 500 and code else f'http_{status}'
            return routes.json_response({'error': code, 'error_code': code}, status)
        return result

    routes.call = call
    if platform == 'shazam':
        tracks = importlib.import_module('shazam.tracks')
        pages = importlib.import_module('shazam.pages')
        tracks.resolve = memoize(tracks.resolve)
        pages.track_page = memoize(pages.track_page)
    elif platform == 'musixmatch':
        cache_musixmatch(importlib.import_module('musixmatch.lookup'))
