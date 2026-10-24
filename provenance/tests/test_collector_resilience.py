from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase

from provenance.collectors.http import Budget, CollectionError, JsonClient
from provenance.collectors.providers import collect_spotify, collect_youtube


class ProviderResilienceTests(SimpleTestCase):
    def test_spotify_optional_failure_and_track_failure_preserve_remaining_catalog(self):
        calls = []
        def fetch(path, **params):
            calls.append((path, params))
            if path == "/artists/details":
                return {"id": "artist"}
            if path == "/artists/discovered-on":
                raise CollectionError("http_502")
            if path == "/artists/appears-on":
                return {"results": [], "total_count": 0}
            if path == "/artists/albums":
                return {"results": [{"id": "album"}]}
            if path == "/albums/details":
                raise CollectionError("http_502")
            if path == "/albums/tracks":
                return {"results": [{"id": key, "artists": [{"id": "artist"}]} for key in ("one", "two")]}
            if params.get("track") == "one":
                raise CollectionError("http_502")
            return {"id": params["track"]}
        rows = list(collect_spotify("https://open.spotify.com/artist/artist", fetch, lambda path: {"id": path.split("/")[-1]}))
        self.assertEqual([r.external_id for r in rows if r.endpoint == "track-details"], ["two"])
        self.assertEqual([r.external_id for r in rows if r.endpoint == "spotify-track"], ["one", "two"])
        failures = [r for r in rows if r.payload.get("error_code")]
        self.assertEqual(len(failures), 3)
        self.assertTrue(all(r.incomplete and r.kind == "collection" for r in failures))

    def test_youtube_failed_video_and_shorts_page_preserve_live_and_playlists(self):
        visited = []
        def fetch(path, **params):
            visited.append(path)
            if path == "/channels/details":
                return {"channel": {"id": "channel"}}
            if path == "/channels/videos":
                return {"videos": [{"id": "bad"}, {"id": "good"}]}
            if path == "/channels/shorts" or params.get("video") == "bad":
                raise CollectionError("http_502")
            if path == "/channels/live":
                return {"videos": [{"id": "live"}]}
            if path == "/channels/playlists":
                return {"playlists": []}
            return {"video": {"id": params["video"]}}
        rows = list(collect_youtube("https://www.youtube.com/@artist", fetch))
        self.assertEqual([r.external_id for r in rows if r.kind == "video"], ["good", "live"])
        self.assertIn("/channels/playlists", visited)
        self.assertEqual(sum(r.incomplete for r in rows), 2)

    def test_request_budget_remains_fatal_on_optional_endpoint(self):
        def fetch(path, **params):
            if path == "/artists/details":
                return {"id": "artist"}
            raise CollectionError("request_limit")
        with self.assertRaisesRegex(CollectionError, "request_limit"):
            list(collect_spotify("https://open.spotify.com/artist/artist", fetch, None))

    def test_transient_official_track_failure_does_not_disable_remaining_tracks(self):
        def fetch(path, **params):
            if path == "/artists/details":
                return {"id": "artist"}
            if path in {"/artists/discovered-on", "/artists/appears-on"}:
                return {"results": []}
            if path == "/artists/albums":
                return {"results": [{"id": "album"}]}
            if path == "/albums/details":
                return {"id": "album"}
            if path == "/albums/tracks":
                return {"results": [{"id": key, "artists": [{"id": "artist"}]} for key in ("one", "two")]}
            return {"id": params["track"]}
        def official(path):
            if path.endswith("one"):
                raise CollectionError("http_502")
            return {"id": "two"}
        rows = list(collect_spotify("https://open.spotify.com/artist/artist", fetch, official))
        self.assertEqual([r.external_id for r in rows if r.endpoint == "spotify-track"], ["two"])
        self.assertTrue(any(r.incomplete for r in rows))

    def test_retry_after_is_a_floor_and_jitter_is_bounded(self):
        client = JsonClient("https://example.invalid", budget=Budget(3))
        with patch.object(client.opener, "open", side_effect=HTTPError("private", 502, "private", {"Retry-After": "2"}, None)), patch("provenance.collectors.http.time.sleep") as sleep, patch("random.uniform", return_value=0.25):
            with self.assertRaisesRegex(CollectionError, "http_502"):
                client.get("/details")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.25, 2.25])

    def test_failed_core_request_carries_only_a_fixed_endpoint_name(self):
        client = JsonClient("https://example.invalid", budget=Budget(3))
        with patch.object(client.opener, "open", side_effect=HTTPError("private?api_key=private", 502, "private", {}, None)), patch("provenance.collectors.http.time.sleep"):
            with self.assertRaises(CollectionError) as raised:
                client.get("/artists/details", artist="private")
        self.assertEqual(raised.exception.endpoint, "artists-details")
        self.assertEqual(str(raised.exception), "http_502")

    def test_retry_cache_never_reuses_a_previous_utc_day(self):
        from datetime import timedelta
        from django.utils import timezone
        from provenance.collectors.providers import SnapshotCache, cached_fetch
        cache = SnapshotCache()
        cache.day = timezone.now().date() - timedelta(days=1)
        cache[("track-details", "track")] = {"id": "track", "playcount": 1}
        with patch("django.utils.timezone.now", return_value=timezone.now()):
            rows = cached_fetch(lambda path, **params: {"id": "track", "playcount": 2}, cache)("/tracks/details", track="track")
        self.assertEqual(rows["playcount"], 2)
        self.assertEqual(cache.hits, 0)

    def test_unknown_error_messages_are_never_receipt_codes(self):
        from provenance.collectors.http import safe_error_code
        self.assertEqual(safe_error_code(CollectionError("PrivateToken123")), "collection_error")
        self.assertEqual(safe_error_code(CollectionError("http_502")), "http_502")

    def test_apple_retry_fingerprint_tracks_effective_storefront(self):
        from provenance.collectors.runner import profile_fingerprint
        us = "https://music.apple.com/us/artist/example/123"
        gb = "https://music.apple.com/gb/artist/example/123"
        self.assertNotEqual(profile_fingerprint("apple-music", us), profile_fingerprint("apple-music", gb))
        self.assertEqual(profile_fingerprint("apple-music", us), profile_fingerprint("apple-music", us.replace("/us/", "/US/")))

    def test_spotify_failed_profile_still_collects_verified_artist_discography(self):
        from provenance.collectors.providers import collect_spotify
        def fetch(path, **params):
            if path == "/artists/details":
                raise CollectionError("http_502")
            if path in {"/artists/discovered-on", "/artists/appears-on"}:
                return {"results": []}
            if path == "/artists/albums":
                self.assertEqual(params["artist"], "artist")
                return {"results": [{"id": "album"}]}
            if path == "/albums/details":
                return {"id": "album"}
            if path == "/albums/tracks":
                return {"results": [{"id": "track", "artists": [{"id": "artist"}]}]}
            return {"id": "track"}
        rows = list(collect_spotify("https://open.spotify.com/artist/artist", fetch, lambda path: {"id": "track"}))
        self.assertFalse(any(row.kind == "artist" for row in rows))
        self.assertTrue(any(row.endpoint == "artist-details-unavailable" and row.incomplete for row in rows))
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["track", "track"])

    def test_apple_failed_profile_still_collects_verified_artist_songs(self):
        from provenance.collectors.providers import collect_apple
        def fetch(path, **params):
            self.assertEqual(params["country"], "GB")
            if path == "/artists/details":
                raise CollectionError("http_502")
            if path == "/artists/albums":
                self.assertEqual(params["artist"], "artist")
                return {"albums": [{"id": "album"}]}
            if path == "/albums/details":
                return {"id": "album", "tracks": [{"id": "track"}]}
            if path == "/songs/details":
                return {"id": "track", "artists": [{"id": "artist"}]}
            return {"music_videos" if path.endswith("music-videos") else "playlists": []}
        rows = list(collect_apple("https://music.apple.com/gb/artist/example/artist", fetch))
        self.assertFalse(any(row.kind == "artist" for row in rows))
        self.assertTrue(any(row.endpoint == "artist-details-unavailable" and row.incomplete for row in rows))
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["track"])

    def test_soundcloud_failed_profile_still_collects_public_uploads_by_username(self):
        from provenance.collectors.providers import collect_soundcloud
        def fetch(path, **params):
            if path == "/users/details":
                raise CollectionError("http_502")
            if path == "/users/tracks":
                self.assertEqual(params["user"], "verified-user")
                return {"tracks": [{"id": 42}]}
            if path == "/tracks/details":
                return {"id": 42}
            return {"playlists": []} if path == "/users/playlists" else {"items": []}
        rows = list(collect_soundcloud("https://soundcloud.com/verified-user", fetch))
        self.assertFalse(any(row.kind == "user" for row in rows))
        self.assertTrue(any(row.endpoint == "user-details-unavailable" and row.incomplete for row in rows))
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["42"])

    def test_profile_account_failures_and_budget_exhaustion_stay_fatal(self):
        from provenance.collectors.providers import collect_apple, collect_soundcloud, collect_spotify
        for code in ("request_limit", "missing_omkar_credentials", "http_401", "http_403", "provider_error"):
            def fetch(path, **params):
                raise CollectionError(code)
            for iterator in (
                collect_spotify("https://open.spotify.com/artist/artist", fetch, None),
                collect_apple("https://music.apple.com/us/artist/example/artist", fetch),
                collect_soundcloud("https://soundcloud.com/verified-user", fetch),
            ):
                with self.subTest(code=code, provider=iterator.gi_code.co_name), self.assertRaisesRegex(CollectionError, code):
                    list(iterator)
