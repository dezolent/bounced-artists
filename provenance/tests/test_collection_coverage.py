from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from provenance.collectors.http import Budget, CollectionError
from provenance.collectors.providers import collect_instagram, optional_spotify_collection


class CollectionCoverageTests(SimpleTestCase):
    def test_large_profile_budget_cannot_spend_another_profiles_allowance(self):
        total = Budget(6)
        first = Budget(3, parent=total)
        for _ in range(3):
            first.take()
        with self.assertRaisesRegex(CollectionError, "job_request_limit"):
            first.take()
        second = Budget(3, parent=total)
        second.take()
        self.assertEqual((total.used, first.used, second.used), (4, 3, 1))

    def test_nonpaginated_spotify_shelves_report_provider_limit_separately(self):
        rows = list(optional_spotify_collection(lambda *a, **k: {
            "results": [{"id": "playlist"}], "total_count": 20,
        }, "/artists/discovered-on", "artist"))
        row, items = rows[0]
        self.assertFalse(row.incomplete)
        self.assertTrue(row.limited)
        self.assertEqual(row.payload["total_count"], 20)

    def test_instagram_full_list_items_store_statistics_without_duplicate_detail_calls(self):
        post = {"id": "91", "code": "post", "owner": {"id": "42"},
                "link": "https://www.instagram.com/p/post/", "type": "image", "product_type": "post",
                "caption": "Artist update", "published_at": "2026-10-23T12:00:00Z", "is_caption_edited": False,
                "image": None, "video": None, "width": 1080, "height": 1080, "accessibility_caption": None,
                "title": None, "are_likes_hidden": False, "are_comments_disabled": False,
                "is_paid_partnership": False, "is_pinned": False, "location": None, "audio": None,
                "tagged_users": [], "coauthors": [], "sponsors": [], "carousel": [], "carousel_count": None,
                "stats": {"like_count": 0, "comment_count": 2, "play_count": None,
                          "view_count": None, "repost_count": None}}
        calls = []
        def fetch(path, **params):
            calls.append(path)
            if path == "/users/profile":
                return {"id": "42", "is_private": False}
            if path in {"/users/posts", "/users/reels"}:
                return {"posts" if path.endswith("posts") else "reels": [post]}
            raise AssertionError("Full list items already expose all public counters")
        rows = list(collect_instagram("https://www.instagram.com/artist/", fetch))
        posts = [r for r in rows if r.kind == "post"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].endpoint, "post-list-item")
        self.assertEqual(posts[0].payload["stats"]["like_count"], 0)
        self.assertEqual(posts[0].payload["_collection"]["endpoint"], "users-posts")
        self.assertNotIn("/posts/details", calls)

    def test_instagram_partial_list_item_still_retains_counters_when_details_fail(self):
        def fetch(path, **params):
            if path == "/users/profile":
                return {"id": "42", "is_private": False}
            if path == "/users/posts":
                return {"posts": [{"id": "91", "code": "post", "stats": {"like_count": 5}}]}
            if path == "/users/reels":
                return {"reels": []}
            raise CollectionError("http_502")
        rows = list(collect_instagram("https://www.instagram.com/artist/", fetch))
        self.assertEqual([r.payload["stats"]["like_count"] for r in rows if r.kind == "post"], [5])
        self.assertTrue(any(r.incomplete for r in rows))

    def test_complete_counters_do_not_skip_details_when_post_metadata_is_missing(self):
        post = {"id": "91", "code": "post", "owner": {"id": "42"}, "stats": {
            "like_count": 0, "comment_count": 2, "play_count": None, "view_count": None, "repost_count": None}}
        def fetch(path, **params):
            if path == "/users/profile":
                return {"id": "42", "is_private": False}
            if path in {"/users/posts", "/users/reels"}:
                return {"posts" if path.endswith("posts") else "reels": [post]}
            return {**post, "caption": "Additional detail"}
        rows = list(collect_instagram("https://www.instagram.com/artist/", fetch))
        self.assertTrue(any(r.endpoint == "post-details" and r.payload.get("caption") == "Additional detail" for r in rows))

    def test_failed_cursor_page_retries_the_same_cursor_without_dropping_items(self):
        from provenance.collectors.providers import pages
        calls = []
        def fetch(**params):
            calls.append(params.copy())
            if len(calls) == 2:
                raise CollectionError("http_502")
            return {"posts": [{"id": len(calls)}], "next_cursor": "second" if len(calls) == 1 else None}
        with patch("provenance.collectors.providers.time.sleep"):
            rows = list(pages(fetch, "posts", cursor=True))
        self.assertEqual(len(rows), 2)
        self.assertEqual(calls[1], calls[2])


class CollectionRunCoverageTests(TestCase):
    def setUp(self):
        from artists.models import Artist, ArtistProfileLink, Organization, Service
        from provenance.models import Connection, SyncJob
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Dezolent", slug="coverage", bootstrap_key="dezolent", management_status="managed")
        self.service = Service.objects.create(name="Spotify", slug="spotify")
        link = ArtistProfileLink.objects.create(artist=self.artist, platform=self.service,
            url="https://open.spotify.com/artist/artist", verification_status="verified")
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service, status="active")
        self.job = SyncJob.objects.create(connection=conn, job_type="daily_public_platform", enabled=True,
            cursor_json={"profile_link_id": str(link.pk)})

    def test_run_records_provider_limit_without_retrying_an_unpageable_shelf(self):
        from provenance.collectors.runner import run_due
        from provenance.collectors.providers import Collected
        from provenance.models import SyncRun
        row = Collected("artist-discovered-on", "collection", "artist",
            {"results": [{"id": "playlist"}], "total_count": 20}, limited=True)
        with patch("provenance.collectors.runner.collect", return_value=iter([row])):
            summary = run_due()
        self.assertEqual(summary["succeeded"], 1)
        run = SyncRun.objects.get()
        self.assertEqual(run.counts_json["provider_limits"]["artist-discovered-on"], {"returned": 1, "reported_total": 20})
        self.assertFalse(run.counts_json["incomplete"])
        self.job.refresh_from_db()
        self.assertIsNotNone(self.job.next_run_at)

    def test_exhausted_job_does_not_starve_next_profile(self):
        from provenance.collectors.runner import run_due
        from provenance.models import SyncJob
        SyncJob.objects.create(connection=self.job.connection, job_type="daily_public_platform", enabled=True,
            cursor_json=self.job.cursor_json)
        calls = []
        def collect(platform, url, budget, **kwargs):
            calls.append(budget)
            if len(calls) == 1:
                for _ in range(3):
                    budget.take()
            else:
                budget.take()
            return iter([])
        with patch("provenance.collectors.runner.collect", side_effect=collect):
            summary = run_due(request_limit=6, job_request_limit=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual((summary["succeeded"], summary["failed"], summary["requests"]), (1, 1, 3))

    def test_existing_same_day_evidence_backfills_a_new_song_without_another_snapshot(self):
        from django.utils import timezone
        from catalog.models import Recording
        from provenance.collectors.runner import persist
        from provenance.collectors.providers import Collected
        from provenance.models import SourceRecord, SyncRun
        row = Collected("spotify-track", "track", "track", {"id": "track", "name": "New song", "artists": [{"id": "artist"}]})
        run = SyncRun.objects.create(sync_job=self.job)
        now = timezone.now()
        with patch("provenance.collectors.catalog.ensure_catalog_recording", return_value=False):
            persist(run, row, now)
        self.assertEqual(Recording.objects.count(), 0)
        self.assertFalse(persist(run, row, now))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(SourceRecord.objects.count(), 1)

    def test_discovered_song_is_visible_in_its_artist_catalog_with_metrics(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone
        from artists.models import ArtistMembership
        from catalog.models import Recording
        from provenance.collectors.runner import persist
        from provenance.collectors.providers import Collected
        from provenance.models import SyncRun
        row = Collected("track-details", "track", "track", {"id": "track", "name": "Discovered song",
            "artists": [{"id": "artist"}], "playcount": 41})
        run = SyncRun.objects.create(sync_job=self.job)
        persist(run, row, timezone.now())
        recording = Recording.objects.get()
        user = get_user_model().objects.create_user(username="discovered-song-viewer")
        ArtistMembership.objects.create(user=user, artist=self.artist, role="viewer")
        self.client.force_login(user)
        response = self.client.get(reverse("portal:recording", args=[self.artist.pk, recording.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Discovered song")
        self.assertContains(response, "41")
        self.assertContains(response, "All song insights")


    def test_elapsed_profile_limit_preserves_saved_rows_and_runs_next_profile(self):
        from provenance.collectors.runner import run_due
        from provenance.collectors.providers import Collected
        from provenance.models import SourceRecord, SyncJob, SyncRun
        SyncJob.objects.create(connection=self.job.connection, job_type="daily_public_platform", enabled=True,
            cursor_json=self.job.cursor_json)
        clock = [0]
        calls = []
        def collect(platform, url, budget, **kwargs):
            calls.append(budget)
            yield Collected("artist-details", "artist", str(len(calls)), {"id": str(len(calls))})
            if len(calls) == 1:
                clock[0] = 6
                yield Collected("artist-details", "artist", "unsaved", {"id": "unsaved"})
        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("provenance.collectors.runner.collect", side_effect=collect):
            summary = run_due(time_limit=100, job_time_limit=5)
        self.assertEqual((summary["failed"], summary["succeeded"]), (1, 1))
        self.assertEqual(SourceRecord.objects.count(), 2)
        self.assertFalse(SourceRecord.objects.filter(external_id="unsaved").exists())
        failed = SyncRun.objects.get(status="failed")
        self.assertEqual(failed.error_summary, "job_time_limit")
        self.assertEqual(failed.counts_json["elapsed_seconds"], 6)
        self.assertIn("persist_seconds", failed.counts_json)

    def test_invocation_deadline_leaves_unstarted_jobs_due(self):
        from provenance.collectors.runner import run_due
        from provenance.collectors.providers import Collected
        from provenance.models import SyncJob, SyncRun
        SyncJob.objects.create(connection=self.job.connection, job_type="daily_public_platform", enabled=True,
            cursor_json=self.job.cursor_json)
        clock = [0]
        def collect(*args, **kwargs):
            yield Collected("artist-details", "artist", "one", {"id": "one"})
            clock[0] = 6
            yield Collected("artist-details", "artist", "two", {"id": "two"})
        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("provenance.collectors.runner.collect", side_effect=collect):
            summary = run_due(time_limit=5, job_time_limit=100)
        self.assertTrue(summary["time_limit_reached"])
        self.assertEqual(SyncRun.objects.count(), 1)
        self.assertEqual(SyncRun.objects.get().error_summary, "time_limit")
        self.assertEqual(SyncJob.objects.filter(next_run_at__isnull=True).count(), 2)
