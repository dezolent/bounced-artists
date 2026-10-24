from io import StringIO
import json

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from artists.models import Artist, ArtistProfileLink, Organization, Service
from provenance.collectors.http import CollectionError
from provenance.collectors.providers import Collected, profile_locator
from provenance.models import SourceRecord, SyncJob, SyncRun


class TikTokLocatorTests(SimpleTestCase):
    def test_verified_profile_url_has_a_canonical_username(self):
        self.assertEqual(profile_locator("tiktok", "https://www.tiktok.com/@SRY4U_?lang=en"), "sry4u_")

    def test_only_direct_profile_urls_qualify(self):
        for url in ("https://www.tiktok.com/@artist/video/123", "https://evil.invalid/@artist",
                    "https://www.tiktok.com/music/title-123", "https://www.tiktok.com/artist",
                    "https://www.tiktok.com/@", "https://vm.tiktok.com/short", "http://www.tiktok.com/@artist"):
            with self.subTest(url=url), self.assertRaises(CollectionError):
                profile_locator("tiktok", url)


class TikTokIntegrationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, bootstrap_key="dezolent", name="Dezolent",
            slug="tiktok-integration", management_status="managed")
        self.service = Service.objects.create(name="TikTok", slug="tiktok")
        self.link = ArtistProfileLink.objects.create(artist=self.artist, platform=self.service,
            url="https://www.tiktok.com/@dezolent", verification_status="verified")

    def configure(self):
        call_command("configure_artist_sync", apply=True, stdout=StringIO())
        return SyncJob.objects.get(connection__artist=self.artist)

    def test_verified_customer_profile_configures_and_unverified_guest_does_not(self):
        guest = Artist.objects.create(organization=self.org, name="Guest", slug="tiktok-guest", management_status="managed")
        ArtistProfileLink.objects.create(artist=guest, platform=self.service,
            url="https://www.tiktok.com/@guest", verification_status="verified")
        job = self.configure()
        self.assertEqual(SyncJob.objects.count(), 1)
        self.assertEqual(job.cursor_json["profile_link_id"], str(self.link.pk))
        self.link.verification_status = "unverified"
        self.link.save()
        call_command("configure_artist_sync", apply=True, stdout=StringIO())
        job.refresh_from_db()
        self.assertFalse(job.enabled)

    def test_video_metrics_use_root_payload_and_canonical_views(self):
        from analytics.models import MetricObservation
        from catalog.models import PlatformObject, Recording
        from provenance.collectors.runner import persist
        run = SyncRun.objects.create(sync_job=self.configure())
        payload = {"id": "123", "link": "https://www.tiktok.com/@dezolent/video/123", "title": "Release teaser",
            "author": {"id": "42", "username": "dezolent"}, "video": {"duration_seconds": 15},
            "stats": {"play_count": 100, "like_count": 8, "comment_count": 1, "share_count": 2, "save_count": 0}}
        self.assertTrue(persist(run, Collected("tiktok-video", "video", "123", payload), timezone.now()))
        obj = PlatformObject.objects.get()
        self.assertEqual(obj.title, "Release teaser")
        values = dict(MetricObservation.objects.values_list("series__metric_definition__code", "value"))
        self.assertEqual(values["view_count"], 100)
        self.assertEqual(values["share_count"], 2)
        self.assertEqual(values["save_count"], 0)
        self.assertNotIn("play_count", values)
        self.assertEqual(Recording.objects.count(), 0)

    def test_fan_usage_does_not_inflate_owned_video_or_song_play_totals(self):
        from django.contrib.auth import get_user_model
        from artists.models import ArtistMembership
        from portal.dashboard import dashboard_for
        from provenance.collectors.runner import persist
        run = SyncRun.objects.create(sync_job=self.configure(), status="succeeded")
        for row in (
            Collected("tiktok-video", "video", "123", {"id": "123", "stats": {"play_count": 100, "share_count": 2}}),
            Collected("tiktok-usage-video", "usage_video", "456", {"id": "456", "stats": {"play_count": 5000}, "_collection": {"music_id": "789"}}),
            Collected("tiktok-music", "music", "789", {"id": "789", "title": "Artist sound", "video_count": 200}),
        ):
            persist(run, row, timezone.now())
        user = get_user_model().objects.create_user(username="tiktok-dashboard")
        ArtistMembership.objects.create(artist=self.artist, user=user, role="viewer")
        result = dashboard_for(user, self.artist)
        platform = next(p for p in result["platforms"] if p["slug"] == "tiktok")
        self.assertEqual(result["headline"]["views"]["value"], 100)
        self.assertIsNone(result["headline"]["plays"]["value"])
        self.assertEqual(platform["metrics"]["music_uses"]["value"], 200)
        self.assertEqual(platform["metrics"]["usage_views"]["value"], 5000)
        self.assertEqual(platform["metrics"]["shares"]["value"], 2)

    def test_command_preview_exposes_the_six_platform_budget_without_network(self):
        out = StringIO()
        call_command("sync_artist_platforms", stdout=out)
        preview = json.loads(out.getvalue())
        self.assertEqual(preview["request_limit"], 18000)
        self.assertEqual(preview["job_request_limit"], 1000)
        self.assertFalse(SourceRecord.objects.exists())

    def test_failed_retry_does_not_reuse_partial_list_item_as_video_details(self):
        from provenance.collectors.runner import persist, profile_fingerprint, retry_snapshots
        job = self.configure()
        now = timezone.now()
        fingerprint = profile_fingerprint("tiktok", self.link.url)
        run = SyncRun.objects.create(sync_job=job, status="failed", started_at=now,
            counts_json={"profile_fingerprint": fingerprint})
        persist(run, Collected("tiktok-video", "video", "123", {"id": "123", "stats": {"play_count": 50},
            "_collection": {"endpoint": "tiktok-users-videos"}}), now)
        persist(run, Collected("tiktok-video", "video", "456", {"id": "456", "stats": {"play_count": 100},
            "_collection": {"endpoint": "tiktok-videos-details"}}), now)
        cache = retry_snapshots(job, now, False, fingerprint=fingerprint)
        self.assertNotIn(("tiktok-video", "123"), cache)
        self.assertIn(("tiktok-video", "456"), cache)

    def test_tiktok_insights_renders_planning_and_keeps_non_tiktok_views_unchanged(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from artists.models import ArtistMembership
        user = get_user_model().objects.create_user(username="tiktok-insights-viewer")
        ArtistMembership.objects.create(artist=self.artist, user=user, role="viewer")
        self.client.force_login(user)
        url = reverse("portal:insights", args=[self.artist.pk])
        response = self.client.get(url, {"platform": "tiktok"})
        self.assertContains(response, "Uploads &amp; sound usage")
        self.assertContains(response, "No verified artist sounds collected yet")
        self.assertNotContains(self.client.get(url, {"platform": "spotify"}), "PUBLIC TIKTOK PERFORMANCE")

    def test_music_album_text_does_not_break_insights_or_source_details(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from artists.models import ArtistMembership
        from provenance.collectors.runner import persist
        run = SyncRun.objects.create(sync_job=self.configure())
        persist(run, Collected("tiktok-music", "music", "789", {"id": "789", "title": "Artist sound",
            "album": "Artist album", "video_count": 200}), timezone.now())
        user = get_user_model().objects.create_user(username="tiktok-album-viewer")
        ArtistMembership.objects.create(artist=self.artist, user=user, role="viewer")
        self.client.force_login(user)
        response = self.client.get(reverse("portal:insights", args=[self.artist.pk]), {"platform": "tiktok"})
        self.assertContains(response, "Artist sound")
        source = SourceRecord.objects.get()
        response = self.client.get(reverse("portal:insight-detail", args=[self.artist.pk, source.pk]))
        self.assertContains(response, "Artist album")

    def test_unchanged_music_snapshot_can_link_a_newly_accepted_catalog_destination(self):
        from analytics.models import MetricObservation
        from artists.models import ArtistResourceGrant
        from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
        from provenance.collectors.runner import persist
        job = self.configure()
        run = SyncRun.objects.create(sync_job=job)
        service = Service.objects.create(slug="spotify", name="Spotify")
        recording = Recording.objects.create(organization=self.org, title="Artist song")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        spotify = PlatformObject.objects.create(service=service, object_type="track", external_id="spotify-track")
        match = RecordingPlatformMatch.objects.create(recording=recording, platform_object=spotify)
        row = Collected("tiktok-music", "music", "789", {"id": "789", "title": "Artist song", "video_count": 200,
            "streaming_links": [{"platform": "spotify", "song_id": "spotify-track"}]})
        now = timezone.now()
        persist(run, row, now)
        self.assertFalse(RecordingPlatformMatch.objects.filter(platform_object__service=self.service).exists())
        match.review_status = "accepted"
        match.save()
        self.assertFalse(persist(run, row, now))
        self.assertTrue(RecordingPlatformMatch.objects.filter(platform_object__service=self.service,
            recording=recording, review_status="accepted").exists())
        self.assertEqual((SourceRecord.objects.count(), MetricObservation.objects.count()), (1, 1))
