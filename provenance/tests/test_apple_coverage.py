from collections import Counter
from unittest.mock import patch

from django.test import SimpleTestCase

from provenance.collectors.http import CollectionError
from provenance.collectors.providers import collect_apple


class AppleCoverageTests(SimpleTestCase):
    def collect(self, responses):
        calls = []

        def fetch(path, **params):
            calls.append((path, params))
            if path == "/songs/details":
                response = responses.get((path, params["song"]), {
                    "id": params["song"], "artists": [{"id": "42"}],
                })
            elif path == "/albums/details":
                response = responses[(path, params["album"])]
            else:
                response = responses.get((path, params.get("page", 1)), {
                    "/artists/details": {"id": "42"},
                    "/artists/albums": {"albums": []},
                    "/artists/top-songs": {"songs": []},
                    "/artists/music-videos": {"music_videos": []},
                    "/artists/playlists": {"playlists": []},
                }[path])
            if isinstance(response, Exception):
                raise response
            return response

        with patch("provenance.collectors.providers.time.sleep"):
            return list(collect_apple("https://music.apple.com/gb/artist/customer/42", fetch)), calls

    def test_top_songs_pages_recover_missing_album_songs_and_deduplicate_details(self):
        rows, calls = self.collect({
            ("/artists/details", 1): {"id": "42", "top_songs": [{"id": "81"}]},
            ("/artists/albums", 1): CollectionError("http_502"),
            ("/artists/top-songs", 1): {
                "songs": [{"id": "81"}, {"id": "82"}], "total_pages": 2,
                "next": "https://untrusted.invalid/next",
            },
            ("/artists/top-songs", 2): {"songs": [{"id": "83"}], "total_pages": 2},
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["81", "82", "83"])
        self.assertEqual(Counter(params["song"] for path, params in calls if path == "/songs/details"), {"81": 1, "82": 1, "83": 1})
        self.assertTrue(any(row.endpoint == "artists-albums-unavailable" and row.incomplete for row in rows))
        top_calls = [params for path, params in calls if path == "/artists/top-songs"]
        self.assertEqual([params["page"] for params in top_calls], [1, 2])
        self.assertTrue(all(params["limit"] == 100 and params["artist"] == "42" for params in top_calls))
        self.assertTrue(all(params["country"] == "GB" for _, params in calls))

    def test_partial_album_keeps_evidence_and_top_songs_fill_missing_tracks(self):
        rows, calls = self.collect({
            ("/artists/albums", 1): {"albums": [{"id": "71"}]},
            ("/albums/details", "71"): {"id": "71", "track_count": 2, "tracks": [{"id": "81", "type": "song"}]},
            ("/artists/top-songs", 1): {"songs": [{"id": "81"}, {"id": "82"}]},
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["81", "82"])
        self.assertTrue(next(row for row in rows if row.kind == "album").incomplete)
        self.assertEqual(sum(path == "/songs/details" for path, _ in calls), 2)
        self.assertEqual(next(params for path, params in calls if path == "/artists/albums")["limit"], 100)

    def test_profile_seeds_survive_failed_album_details_and_top_songs_endpoint(self):
        rows, _ = self.collect({
            ("/artists/details", 1): {"id": "42", "top_songs": [{"id": "81"}]},
            ("/artists/albums", 1): {"albums": [{"id": "71"}]},
            ("/albums/details", "71"): CollectionError("http_502"),
            ("/artists/top-songs", 1): CollectionError("http_503"),
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["81"])
        self.assertTrue(any(row.endpoint == "album-details-unavailable" and row.incomplete for row in rows))
        self.assertTrue(any(row.endpoint == "artists-top-songs-unavailable" and row.incomplete for row in rows))

    def test_top_songs_work_when_profile_unavailable(self):
        rows, _ = self.collect({
            ("/artists/details", 1): CollectionError("http_502"),
            ("/artists/top-songs", 1): {"songs": [{"id": "81"}]},
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["81"])
        self.assertTrue(any(row.endpoint == "artist-details-unavailable" and row.incomplete for row in rows))

    def test_later_top_songs_page_failure_keeps_earlier_songs_and_incomplete_receipt(self):
        rows, _ = self.collect({
            ("/artists/top-songs", 1): {"songs": [{"id": "81"}], "total_pages": 2},
            ("/artists/top-songs", 2): {"songs": None},
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["81"])
        self.assertTrue(any(row.endpoint == "artists-top-songs-unavailable" and row.incomplete for row in rows))

    def test_discovery_and_matching_names_never_substitute_for_exact_artist_id(self):
        rows, _ = self.collect({
            ("/artists/top-songs", 1): {"songs": [{"id": key} for key in ["81", "82", "83", "84"]]},
            ("/songs/details", "81"): {"id": "81", "artist_name": "Customer", "artists": [{"id": "142", "name": "Customer"}]},
            ("/songs/details", "82"): {"id": "82", "artist_name": "Customer"},
            ("/songs/details", "83"): {"id": "83", "artists": {"id": "42"}},
            ("/songs/details", "84"): {"id": "84", "artists": [{"id": 42}]},
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["84"])
        self.assertEqual({row.external_id for row in rows if row.endpoint == "song-artist-unconfirmed" and row.incomplete}, {"82", "83"})

    def test_song_failure_is_retained_without_duplicate_detail_retry(self):
        rows, calls = self.collect({
            ("/artists/details", 1): {"id": "42", "top_songs": [{"id": "81"}]},
            ("/artists/top-songs", 1): {"songs": [{"id": "81"}, {"id": "82"}]},
            ("/songs/details", "81"): CollectionError("http_502"),
        })
        self.assertEqual([row.external_id for row in rows if row.kind == "track"], ["82"])
        self.assertEqual(sum(path == "/songs/details" and params["song"] == "81" for path, params in calls), 1)
        self.assertTrue(any(row.endpoint == "song-details-unavailable" and row.external_id == "81" and row.incomplete for row in rows))
