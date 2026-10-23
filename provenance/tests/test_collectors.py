from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from artists.models import Artist, ArtistProfileLink, Organization, Service
from provenance.models import SourceRecord, SyncJob, SyncRun


class TransportTests(SimpleTestCase):
    def test_provider_contracts_collect_all_pages_and_stable_detail_ids(self):
        from provenance.collectors.providers import collect_soundcloud, collect_apple, collect_youtube, collect_instagram
        def fetch(path, **params):
            if path == "/users/details":
                return {"id": 42, "stats": {"followers": 1}}
            if path == "/users/tracks":
                return {"tracks": [{"id": 51 if not params.get("cursor") else 52}], "next_cursor": None if params.get("cursor") else "page-two", "has_more": not bool(params.get("cursor"))}
            if path == "/tracks/details":
                return {"id": int(params["track"]), "title": "A track", "stats": {"plays": 12}, "secret_token": None}
            if path == "/users/playlists":
                return {"playlists": [], "next": None}
            if path in {"/users/spotlight", "/users/social-links"}:
                return {"items": []}
            if path == "/artists/details":
                return {"id": "42", "name": "Apple artist"}
            if path == "/artists/albums":
                return {"albums": [{"id": "71"}], "total_pages": 1}
            if path == "/albums/details":
                return {"id": "71", "track_count": 2, "tracks": [{"id": "81", "type": "song"}, {"id": "82", "type": "song"}]}
            if path == "/songs/details":
                return {"id": params["song"], "name": "Apple song", "artists": [{"id": "42"}]}
            if path == "/artists/music-videos":
                return {"music_videos": []}
            if path == "/artists/playlists":
                return {"playlists": []}
            if path == "/channels/details":
                return {"channel": {"id": "UCtest", "subscriber_count": 3}}
            if path in {"/channels/videos", "/channels/live"}:
                return {"videos": [{"id": "video1"}], "next_cursor": None}
            if path == "/channels/shorts":
                return {"shorts": [{"id": "video2"}], "next_cursor": None}
            if path == "/channels/playlists":
                return {"playlists": [], "next_cursor": None}
            if path == "/videos/details":
                return {"video": {"id": params["video"], "view_count": 20}}
            if path == "/users/profile":
                return {"id": "42", "is_private": False, "follower_count": 0}
            if path in {"/users/posts", "/users/reels"}:
                return {"posts" if path.endswith("posts") else "reels": [{"id": "91", "code": "abc"}], "next_cursor": None, "has_more": False}
            if path == "/posts/details":
                return {"id": "91", "code": "abc", "stats": {"like_count": 9}}
            raise AssertionError(path)
        self.assertEqual([r.external_id for r in collect_soundcloud("https://soundcloud.com/artist", fetch) if r.kind == "track"], ["51", "52"])
        self.assertEqual([r.external_id for r in collect_apple("https://music.apple.com/us/artist/artist/42", fetch) if r.kind == "track"], ["81", "82"])
        self.assertEqual([r.external_id for r in collect_youtube("https://www.youtube.com/@artist", fetch) if r.kind == "video"], ["video1", "video2"])
        self.assertEqual([r.external_id for r in collect_instagram("https://www.instagram.com/artist/", fetch) if r.kind == "post"], ["91"])

    def test_request_budget_stops_retries_and_auth_uses_documented_header(self):
        from provenance.collectors.http import Budget, JsonClient, CollectionError, omkar_client
        from urllib.error import HTTPError
        budget = Budget(2)
        client = JsonClient("https://spotify-scraper.omkar.cloud", {"API-Key": "private"}, budget)
        def respond(request, **kwargs):
            self.assertEqual(request.get_header("Api-key"), "private")
            raise HTTPError(request.full_url, 503, "unavailable", {}, None)
        with patch.object(client.opener, "open", side_effect=respond), patch("provenance.collectors.http.time.sleep"):
            with self.assertRaisesRegex(CollectionError, "request_limit"):
                client.get("/artists/details", artist="artist")
        self.assertEqual(budget.used, 2)

    def test_credentials_never_follow_redirects_or_provider_next_urls(self):
        from provenance.collectors.http import JsonClient, CollectionError
        client = JsonClient("https://spotify-scraper.omkar.cloud", {"API-Key": "private"})
        with self.assertRaises(CollectionError):
            client.get("https://attacker.invalid/steal")

    def test_repeated_cursor_is_a_failure_not_a_complete_catalog(self):
        from provenance.collectors.providers import pages
        from provenance.collectors.http import CollectionError
        with self.assertRaises(CollectionError):
            list(pages(lambda **params: {"tracks": [], "next_cursor": "same", "has_more": True}, "tracks", cursor=True))

    def test_spotify_official_api_failure_preserves_other_track_scrapes(self):
        from provenance.collectors.providers import collect_spotify
        from provenance.collectors.http import CollectionError
        def fetch(path, **params):
            if path == "/artists/details":
                return {"id": "artist"}
            if path in {"/artists/discovered-on", "/artists/appears-on"}:
                return {"results": [], "total_count": 0}
            if path == "/artists/albums":
                return {"results": [{"id": "album"}], "total_pages": 1}
            if path == "/albums/details":
                return {"id": "album"}
            if path == "/albums/tracks":
                return {"results": [{"id": "track1", "artists": [{"id": "artist"}]}, {"id": "track2", "artists": [{"id": "artist"}]}]}
            return {"id": params["track"]}
        def denied(path, **params):
            raise CollectionError("http_403")
        rows = list(collect_spotify("https://open.spotify.com/artist/artist", fetch, denied))
        self.assertEqual([r.external_id for r in rows if r.endpoint == "track-details"], ["track1", "track2"])
        self.assertTrue(any(r.incomplete for r in rows))


class DailySyncTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Collector Artist", slug="collector", bootstrap_key="dezolent", management_status="managed", bio_short="Editorial biography")
        self.service = Service.objects.create(name="Spotify", slug="spotify")
        self.link = ArtistProfileLink.objects.create(artist=self.artist, platform=self.service, external_id="a" * 22, url="https://open.spotify.com/artist/" + "a" * 22, verification_status="verified")

    def configure(self):
        call_command("configure_artist_sync", apply=True, stdout=StringIO())

    def test_setup_requires_verified_managed_profiles_and_is_idempotent(self):
        call_command("configure_artist_sync", stdout=StringIO())
        self.assertEqual(SyncJob.objects.count(), 0)
        self.configure()
        self.configure()
        self.assertEqual(SyncJob.objects.count(), 1)
        self.link.verification_status = "unverified"
        self.link.save()
        self.configure()
        self.assertFalse(SyncJob.objects.get().enabled)

    def test_only_the_three_customer_identities_can_be_collected(self):
        from provenance.collectors.runner import eligible_profiles
        collaborator = Artist.objects.create(organization=self.org, name="Collaborator", slug="collaborator", management_status="managed")
        ArtistProfileLink.objects.create(artist=collaborator, platform=self.service, url="https://open.spotify.com/artist/other", verification_status="verified")
        self.assertEqual(list(eligible_profiles().values_list("artist_id", flat=True)), [self.artist.pk])

    def test_removed_verified_profile_is_not_collected(self):
        from portal.models import OperationRemoval
        from provenance.collectors.runner import eligible_profiles
        OperationRemoval.objects.create(organization=self.org, model_label="artists.artistprofilelink", object_id=str(self.link.pk))
        self.assertFalse(eligible_profiles().exists())

    def test_profile_url_aliases_do_not_create_duplicate_collection_jobs(self):
        ArtistProfileLink.objects.create(artist=self.artist, platform=self.service,
            url=self.link.url + "?si=alias", verification_status="verified")
        self.configure()
        self.assertEqual(SyncJob.objects.filter(enabled=True).count(), 1)

    def test_spotify_expands_album_ids_before_track_enrichment(self):
        from provenance.collectors.providers import collect_spotify
        calls = []
        def omkar(path, **params):
            calls.append((path, params))
            if path == "/artists/details":
                return {"id": "a" * 22, "name": "Artist", "stats": {"followers": 5, "monthly_listeners": 7}}
            if path in ("/artists/albums", "/artists/appears-on"):
                return {"results": [{"id": "b" * 22}], "next": None, "total_pages": 1}
            if path == "/artists/discovered-on":
                return {"results": [], "total_count": 0}
            if path == "/albums/tracks":
                return {"results": [{"id": "c" * 22, "artists": [{"id": "a" * 22}]}], "next": None, "total_pages": 1}
            if path == "/albums/details":
                return {"id": "b" * 22, "name": "Album"}
            if path == "/tracks/details":
                return {"id": "c" * 22, "name": "Song", "playcount": 0}
            raise AssertionError(path)
        def spotify(path, **params):
            self.assertEqual(path, "/tracks/" + "c" * 22)
            return {"id": "c" * 22, "name": "Song", "album": {"id": "b" * 22}}
        rows = list(collect_spotify(self.link.url, omkar, spotify))
        self.assertEqual(sum(r.endpoint == "spotify-track" for r in rows), 1)
        self.assertEqual(sum(path == "/albums/tracks" for path, params in calls), 1)

    def test_evidence_and_zero_metrics_are_idempotent_and_editorial_is_preserved(self):
        from provenance.collectors.providers import Collected
        from provenance.collectors.runner import run_due
        from analytics.models import MetricObservation
        self.configure()
        row = Collected("artist-details", "artist", "a" * 22, {"id": "a" * 22, "name": "Scraped name", "stats": {"followers": 0, "monthly_listeners": None}})
        with patch("provenance.collectors.runner.collect", return_value=iter([row])):
            result = run_due()
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(SourceRecord.objects.count(), 1)
        self.assertEqual(MetricObservation.objects.get().value, 0)
        self.artist.refresh_from_db()
        self.assertEqual(self.artist.bio_short, "Editorial biography")
        self.assertEqual(self.artist.name, "Collector Artist")
        SyncJob.objects.update(next_run_at=None)
        with patch("provenance.collectors.runner.collect", return_value=iter([row])):
            run_due()
        self.assertEqual(SourceRecord.objects.count(), 1)
        self.assertEqual(MetricObservation.objects.count(), 1)

    def test_failure_is_recorded_safely_and_other_jobs_continue(self):
        from provenance.collectors.runner import run_due
        from provenance.collectors.http import CollectionError
        self.configure()
        with patch("provenance.collectors.runner.collect", side_effect=CollectionError("request_limit")):
            result = run_due()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(SyncRun.objects.get().status, "failed")
        self.assertEqual(SyncRun.objects.get().error_summary, "request_limit")

    def test_preview_never_calls_a_provider_and_payload_tokens_are_removed(self):
        from provenance.collectors.runner import safe_payload
        self.configure()
        with patch("provenance.collectors.runner.collect", side_effect=AssertionError("must not fetch")):
            call_command("sync_artist_platforms", stdout=StringIO())
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(safe_payload({"secret_token": "private", "stats": {"plays": 0}, "nested": {"api-key": "private"}}), {"stats": {"plays": 0}, "nested": {}})

    def test_existing_global_object_gets_an_artist_specific_review(self):
        from catalog.models import PlatformObject
        from provenance.models import ReviewCandidate
        from provenance.collectors.providers import Collected
        from provenance.collectors.runner import run_due
        PlatformObject.objects.create(service=self.service, object_type="track", external_id="known")
        self.configure()
        row = Collected("track-details", "track", "known", {"id": "known", "name": "Discovered for this artist"})
        with patch("provenance.collectors.runner.collect", return_value=iter([row])):
            run_due()
        self.assertEqual(ReviewCandidate.objects.filter(target__artist=self.artist).count(), 1)

    def test_changed_daily_observation_is_a_revision_and_failed_snapshot_is_atomic(self):
        from provenance.collectors.runner import run_due
        from provenance.collectors.providers import Collected
        from analytics.models import MetricObservation
        self.configure()
        for followers in (1, 2, 1):
            row = Collected("artist-details", "artist", "a" * 22, {"id": "a" * 22, "stats": {"followers": followers}})
            with patch("provenance.collectors.runner.collect", return_value=iter([row])):
                self.assertEqual(run_due(force=True)["succeeded"], 1)
        self.assertEqual(list(MetricObservation.objects.order_by("revision").values_list("value", flat=True)), [1, 2, 1])
        self.assertEqual(SourceRecord.objects.count(), 3)
        row = Collected("track-details", "track", "bad", {"id": "bad", "playcount": 2})
        with patch("provenance.collectors.runner.collect", return_value=iter([row])), patch("provenance.collectors.runner.record_metrics", side_effect=ValueError("private information")):
            self.assertEqual(run_due(force=True)["failed"], 1)
        self.assertEqual(SourceRecord.objects.count(), 3)
        self.assertEqual(SyncRun.objects.latest("created_at").error_summary, "unexpected_collection_error")

    def test_revoked_profile_is_not_collected(self):
        from provenance.collectors.runner import run_due
        self.configure()
        self.link.verification_status = "rejected"
        self.link.save()
        with patch("provenance.collectors.runner.collect", side_effect=AssertionError("must not fetch")):
            self.assertEqual(run_due()["skipped"], 1)
