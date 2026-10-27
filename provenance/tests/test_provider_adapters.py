"""Private route boundary: safe errors and reuse of shared upstream lookups."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class UpstreamError(Exception):
    pass


class Blocked(UpstreamError):
    pass


class Gated(UpstreamError):
    pass


class ProviderAdapterTests(TestCase):
    def adapters(self):
        spec = importlib.util.spec_from_file_location('provider_adapters',
            Path(__file__).resolve().parents[2] / 'deploy/scrapers/adapters.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def routes(self):
        response = SimpleNamespace(status_code=200)
        def json_response(body, status=200):
            response.status_code = status
            return json.dumps(body)
        # The pinned Instagram call swallows transport exceptions into HTTP 500.
        def call(label, fn):
            try:
                return json_response(fn())
            except Exception as exc:
                return json_response({'error': str(exc)}, 500)
        return SimpleNamespace(call=call, response=response, json_response=json_response)

    def test_gate_is_machine_readable_and_exception_text_never_leaves_route(self):
        module = self.adapters()
        routes = self.routes()
        transport = SimpleNamespace(InstagramBlocked=Blocked, InstagramGated=Gated,
                                    InstagramUpstreamError=UpstreamError)
        with patch.object(module.importlib, 'import_module', return_value=transport):
            module.install('instagram', routes)
        for error, code in [(Gated('secret login URL'), 'provider_login_required'),
                            (Blocked('secret proxy URL'), 'provider_blocked'),
                            (UpstreamError('secret request URL'), 'provider_upstream_error'),
                            (RuntimeError('secret payload'), 'provider_internal_error')]:
            def fail():
                raise error
            result = json.loads(routes.call('users/posts', fail))
            self.assertEqual(result, {'error': code, 'error_code': code})
            self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(json.loads(routes.call('users/reels', lambda: {'reels': []})), {'reels': []})

    def test_shared_lookup_reuse_is_bounded_and_does_not_cache_failures_or_mutations(self):
        module = self.adapters()
        calls = []
        def fetch(value):
            calls.append(value)
            if value == 'bad':
                raise UpstreamError('unavailable')
            return {'value': value, 'items': []}
        clock = [0]
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            cached = module.memoize(fetch, maxsize=2, ttl=10)
            cached('one')['items'].append('modified')
            self.assertEqual(cached('one'), {'value': 'one', 'items': []})
            self.assertEqual(calls, ['one'])
            for _ in range(2):
                with self.assertRaises(UpstreamError):
                    cached('bad')
            cached('two')
            cached('three')
            cached('one')
            self.assertEqual(calls.count('one'), 2)
            clock[0] = 11
            cached('one')
            self.assertEqual(calls.count('one'), 3)

    def test_musixmatch_native_id_lookups_reuse_the_confirmed_spotify_result(self):
        module = self.adapters()
        calls = []
        def track_object(ref):
            calls.append(ref)
            return {'track_id': 77, 'track_spotify_id': 'spotify123'}
        lookup = SimpleNamespace(track_object=track_object)
        module.cache_musixmatch(lookup)
        first = lookup.track_object({'kind': 'spotify_id', 'value': 'spotify123'})
        first['track_id'] = 999
        second = lookup.track_object({'kind': 'track_id', 'value': 77})
        self.assertEqual(second['track_id'], 77)
        self.assertEqual(len(calls), 1)

    def test_lyrics_response_carries_cached_spotify_identity_without_detail_request(self):
        module = self.adapters()
        response = SimpleNamespace(status_code=200)
        calls = []
        def fetch(ref):
            calls.append(ref)
            return {'track_id': 77, 'track_spotify_id': 'spotify123'}
        lookup = SimpleNamespace(track_object=fetch)
        def get_lyrics(track):
            lookup.track_object(track)
            return {'track': {'id': 77}, 'has_lyrics': False, 'lyrics': None}
        def call(path, schema, fn, paginated):
            return json.dumps(fn(track={'kind': 'spotify_id', 'value': 'spotify123'}))
        routes = SimpleNamespace(call=call, response=response, json_response=lambda body, status: json.dumps(body))
        parsers = SimpleNamespace(track_from_api=lambda body: {'id': body['track_id'], 'external_ids': {'spotify_id': body['track_spotify_id']}})
        transport = SimpleNamespace(MusixmatchBlocked=Blocked, MusixmatchGated=Gated, MusixmatchUpstreamError=UpstreamError)
        def importer(name):
            return {'musixmatch.fetch': transport, 'musixmatch.lookup': lookup, 'musixmatch.parsers': parsers}[name]
        with patch.object(module.importlib, 'import_module', side_effect=importer):
            module.install('musixmatch', routes)
            body = json.loads(routes.call('/tracks/lyrics', None, get_lyrics, False))
        self.assertEqual(body['track']['external_ids'], {'spotify_id': 'spotify123'})
        self.assertEqual(len(calls), 1)
