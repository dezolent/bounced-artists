"""TikTok public performance keeps uploads, sound usage and evidence distinct."""
from datetime import timedelta
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import MetricDefinition, MetricObservation, MetricSeries, dimension_hash
from artists.models import Artist, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class TikTokPerformanceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="TikTok Artist", slug="tiktok-performance")
        self.other = Artist.objects.create(organization=self.org, name="Other TikTok Artist", slug="other-tiktok-performance")
        self.service, _ = Service.objects.get_or_create(slug="tiktok", defaults={"name": "TikTok"})
        self.connection = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service)
        self.job = SyncJob.objects.create(connection=self.connection, job_type="daily_public_platform")

    def source(self, endpoint, external_id, *, payload=None, days=0, job=None):
        observed = timezone.now() - timedelta(days=days)
        run = SyncRun.objects.create(sync_job=job or self.job, status="succeeded", started_at=observed)
        return SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=run,
            collector="omkar", collection_method="scraper", object_type=endpoint, external_id=external_id,
            payload_json=payload or {"id": external_id}, payload_hash="a" * 64, observed_at=observed)

    def metrics(self, source, values, *, status="pending", revision=1, kind=None):
        kind = kind or {"tiktok-user": "user", "tiktok-video": "video", "tiktok-music": "music", "tiktok-usage-video": "usage_video"}[source.object_type]
        obj, _ = PlatformObject.objects.get_or_create(service=self.service, object_type=kind, external_id=source.external_id)
        day = source.observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        result = []
        for code, value in values.items():
            definition, _ = MetricDefinition.objects.get_or_create(service=self.service, code=code,
                defaults={"unit": "count", "aggregation_rule": "cumulative", "period_type": "daily_snapshot"})
            series, _ = MetricSeries.objects.get_or_create(organization=self.org, platform_object=obj, metric_definition=definition,
                source_method="omkar", dimensions_hash=dimension_hash({"scope": "public"}), defaults={"dimensions_json": {"scope": "public"}})
            result.append(MetricObservation.objects.create(series=series, source_record=source, period_start=day,
                period_end=day + timedelta(days=1), observed_at=source.observed_at, value=value,
                revision=revision, quality_status=status))
        return result

    def performance(self):
        from portal.tiktok_insights import performance_for
        return performance_for(self.artist)

    def test_latest_owned_snapshot_revisions_and_missing_counters(self):
        old = self.source("tiktok-video", "100", days=1, payload={"description": "Old caption"})
        self.metrics(old, {"view_count": 800})
        current = self.source("tiktok-video", "100", payload={"description": "Current caption", "stats": {"play_count": 99999}, "link": "https://www.tiktok.com/@artist/video/100"})
        self.metrics(current, {"view_count": 100, "like_count": 10, "comment_count": 0, "share_count": 3, "save_count": 2})
        self.metrics(current, {"view_count": 200}, revision=2)
        self.metrics(current, {"view_count": 999}, status="rejected", revision=3)
        missing = self.source("tiktok-video", "101", payload={"description": "Missing saves", "stats": {"save_count": 400}})
        self.metrics(missing, {"view_count": 50, "like_count": 5, "comment_count": 1, "share_count": 0})
        result = self.performance()
        self.assertEqual(result["collected_video_count"], 2)
        first = result["owned_videos"][0]
        self.assertEqual(first["caption"], "Current caption")
        self.assertEqual(first["metrics"]["views"], Decimal(200))
        self.assertEqual(first["engagement_rate"], Decimal("7.5"))
        self.assertIsNone(result["owned_videos"][1]["metrics"]["saves"])
        self.assertIsNone(result["owned_videos"][1]["engagement_rate"])
        self.assertEqual(result["totals"]["views"]["value"], 250)
        self.assertEqual(result["engagement_complete_count"], 1)
        self.assertEqual(result["engagement_rate"], Decimal("7.5"))

    def test_owned_usage_and_global_sound_counts_are_never_combined(self):
        own = self.source("tiktok-video", "100", payload={"description": "Own upload", "music": {"id": "500"}})
        self.metrics(own, {"view_count": 100})
        music = self.source("tiktok-music", "500", payload={"title": "Catalog sound"})
        self.metrics(music, {"video_count": 700})
        for key, views in (("100", 100), ("200", 50)):
            usage = self.source("tiktok-usage-video", key, payload={"description": "Using the sound", "_collection": {"music_id": "500"}, "author": {"username": "public-creator"}})
            self.metrics(usage, {"view_count": views})
        result = self.performance()
        self.assertEqual(result["totals"]["views"]["value"], 100)
        self.assertEqual(result["usage_sample_count"], 1)
        self.assertEqual(result["music"][0]["video_count"], 700)
        self.assertEqual(result["music"][0]["sample_count"], 1)
        self.assertEqual(result["usage_videos"][0]["creator"], "public-creator")

    def test_scoping_rejects_other_artist_non_daily_wrong_kind_and_corrupt_identity(self):
        valid = self.source("tiktok-video", "100")
        rows = self.metrics(valid, {"view_count": 100})
        wrong = PlatformObject.objects.create(service=self.service, object_type="video", external_id="wrong")
        MetricSeries.objects.filter(pk=rows[0].series_id).update(platform_object=wrong)
        other_connection = Connection.objects.create(organization=self.org, artist=self.other, service=self.service)
        other_job = SyncJob.objects.create(connection=other_connection, job_type="daily_public_platform")
        self.metrics(self.source("tiktok-video", "private-other", job=other_job), {"view_count": 999})
        non_daily = SyncJob.objects.create(connection=self.connection, job_type="manual_unrelated")
        self.metrics(self.source("tiktok-video", "non-daily", job=non_daily), {"view_count": 999})
        self.metrics(self.source("tiktok-video", "wrong-kind"), {"view_count": 999}, kind="usage_video")
        result = self.performance()
        self.assertEqual({row["source"].external_id for row in result["owned_videos"]}, {"100", "wrong-kind"})
        self.assertIsNone(result["totals"]["views"]["value"])

    def test_catalog_links_require_accepted_active_granted_music_identity(self):
        music = self.source("tiktok-music", "500", payload={"title": "Same title"})
        obj = PlatformObject.objects.create(service=self.service, object_type="music", external_id="500")
        recording = Recording.objects.create(organization=self.org, title="Catalog exact match")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        match = RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj, review_status="accepted")
        links = self.performance()["music"][0]["recording_links"]
        self.assertEqual([link["recording"] for link in links], [recording])
        self.assertEqual(links[0]["url"], reverse("portal:recording", args=[self.artist.pk, recording.pk]))
        match.review_status = "pending"
        match.save()
        self.assertEqual(self.performance()["music"][0]["recording_links"], [])
        match.review_status = "accepted"
        match.save()
        ArtistResourceGrant.objects.filter(artist=self.artist, recording=recording).delete()
        self.assertEqual(self.performance()["music"][0]["recording_links"], [])

    def test_weighted_rate_uses_complete_positive_view_videos_and_preserves_zero(self):
        for key, views, likes in (("100", 100, 10), ("101", 900, 0), ("102", 0, 5)):
            source = self.source("tiktok-video", key)
            self.metrics(source, {"view_count": views, "like_count": likes, "comment_count": 0, "share_count": 0, "save_count": 0})
        result = self.performance()
        self.assertEqual(result["engagement_rate"], Decimal(1))
        self.assertEqual(result["engagement_complete_count"], 2)
        self.assertIsNone(next(row for row in result["owned_videos"] if row["source"].external_id == "102")["engagement_rate"])

    def test_template_has_empty_state_and_accessible_evidence_links(self):
        empty = render_to_string("portal/includes/tiktok_performance.html", {"tiktok_performance": self.performance(), "artist": self.artist})
        self.assertIn("No TikTok uploads collected yet", empty)
        source = self.source("tiktok-video", "100", payload={"description": "<script>caption</script>", "link": "javascript:alert(1)", "created_at": "2026-10-01T12:00:00Z", "duration_seconds": 12, "hashtags": ["music"]})
        self.metrics(source, {"view_count": 0})
        html = render_to_string("portal/includes/tiktok_performance.html", {"tiktok_performance": self.performance(), "artist": self.artist})
        self.assertIn("&lt;script&gt;caption&lt;/script&gt;", html)
        self.assertNotIn("javascript:", html)
        self.assertIn('scope="col"', html)
        self.assertIn(reverse("portal:insight-detail", args=[self.artist.pk, source.pk]), html)
        self.assertIn("weighted", html)
        self.assertIn("sample", html)

    def test_profile_and_video_metadata_display_only_known_public_fields(self):
        profile = self.source("tiktok-user", "artist", payload={"username": "public_artist", "nickname": "Public artist", "legal_name": "Private legal identity"})
        self.metrics(profile, {"follower_count": 0, "following_count": 4, "like_count": 5, "video_count": 2})
        video = self.source("tiktok-video", "100", payload={"description": "Planning example", "created_at": "2026-10-01T12:00:00Z", "video": {"duration_seconds": 12}, "hashtags": [{"name": "music"}, "release"], "music": {"id": "500"}})
        result = self.performance()
        self.assertEqual(result["profiles"][0]["profile_metrics"][0]["value"], 0)
        row = result["owned_videos"][0]
        self.assertEqual(row["duration_seconds"], 12)
        self.assertEqual(row["posted_at"].date().isoformat(), "2026-10-01")
        self.assertEqual(row["hashtags"], ["music", "release"])
        self.assertEqual(row["music_id"], "500")
        html = render_to_string("portal/includes/tiktok_performance.html", {"tiktok_performance": result, "artist": self.artist})
        self.assertIn("Public artist", html)
        self.assertNotIn("Private legal identity", html)

    def test_unreported_latest_metric_never_borrows_an_older_snapshot(self):
        old = self.source("tiktok-video", "100", days=1)
        self.metrics(old, {"view_count": 900, "save_count": 100})
        self.source("tiktok-video", "100", payload={"stats": {"play_count": 999, "save_count": 200}})
        result = self.performance()
        self.assertIsNone(result["owned_videos"][0]["metrics"]["views"])
        self.assertIsNone(result["owned_videos"][0]["metrics"]["saves"])
        self.assertIsNone(result["engagement_rate"])

    def test_reads_are_batched_for_owned_uploads_and_music(self):
        for key in ("100", "101", "102", "103"):
            self.metrics(self.source("tiktok-video", key), {"view_count": int(key)})
        self.source("tiktok-music", "500")
        from portal.tiktok_insights import performance_for
        with self.assertNumQueries(3):
            result = performance_for(self.artist)
        self.assertEqual(result["collected_video_count"], 4)

    def test_foreign_organization_and_nonpublic_dimensions_are_excluded(self):
        foreign = Organization.objects.create(name="Foreign TikTok", slug="foreign-tiktok-performance")
        source = self.source("tiktok-video", "100")
        rows = self.metrics(source, {"view_count": 100})
        MetricSeries.objects.filter(pk=rows[0].series_id).update(organization=foreign)
        wrong_source = self.source("tiktok-video", "101")
        self.metrics(wrong_source, {"view_count": 999})
        SourceRecord.objects.filter(pk=wrong_source.pk).update(organization=foreign)
        dimensions = self.source("tiktok-video", "102")
        rows = self.metrics(dimensions, {"view_count": 500})
        MetricSeries.objects.filter(pk=rows[0].series_id).update(dimensions_json={"scope": "private"})
        result = self.performance()
        self.assertEqual(result["collected_video_count"], 2)
        self.assertIsNone(result["totals"]["views"]["value"])

    def test_accepted_legacy_numeric_track_identity_links_to_music_without_url(self):
        self.source("tiktok-music", "500", payload={"title": "Native sound"})
        obj = PlatformObject.objects.create(service=self.service, object_type="track", external_id="500")
        recording = Recording.objects.create(organization=self.org, title="Accepted legacy sound")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj, review_status="accepted")
        self.assertEqual([link["recording"] for link in self.performance()["music"][0]["recording_links"]], [recording])

    def test_conflicting_or_nonmusic_native_urls_cannot_attach_catalog_links(self):
        self.source("tiktok-music", "500")
        self.source("tiktok-music", "501")
        recording = Recording.objects.create(organization=self.org, title="Conflicting identity")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        for kind, url in (("music", "https://www.tiktok.com/music/other-501"), ("track", "https://www.tiktok.com/@artist/video/500")):
            obj = PlatformObject.objects.create(service=self.service, object_type=kind, external_id="500", url=url)
            RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj, review_status="accepted")
        self.assertTrue(all(not row["recording_links"] for row in self.performance()["music"]))
