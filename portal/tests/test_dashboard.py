from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import MetricDefinition, MetricObservation, MetricSeries, dimension_hash
from artists.models import Artist, ArtistMembership, Organization, Service
from catalog.models import PlatformObject
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class DashboardTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Dezolent", slug="dashboard-dezolent", bootstrap_key="dezolent", management_status="managed")
        self.other = Artist.objects.create(organization=self.org, name="Morbeato", slug="dashboard-morbeato", bootstrap_key="morbeato", management_status="managed")
        self.user = get_user_model().objects.create_user(username="dashboard-viewer")
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        self.staff = get_user_model().objects.create_user(username="dashboard-staff", is_staff=True)
        self.now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)

    def observation(self, *, artist=None, platform="spotify", kind="track", external="song", code="playcount", value=100,
                    when=None, revision=1, quality="pending", dimensions=None):
        artist = artist or self.artist
        when = when or self.now
        service, _ = Service.objects.get_or_create(slug=platform, defaults={"name": platform.title()})
        conn, _ = Connection.objects.get_or_create(organization=artist.organization, artist=artist, service=service)
        job, _ = SyncJob.objects.get_or_create(connection=conn, job_type="daily_public_platform")
        run = SyncRun.objects.create(sync_job=job, status="failed", started_at=when, finished_at=when, error_summary="http_502")
        endpoint = {"track": "track-details", "artist": "artist-details", "user": "user-details", "channel": "channel-details", "video": "video-details", "post": "post-details"}[kind]
        source = SourceRecord.objects.create(organization=artist.organization, service=service, sync_run=run,
            collector="omkar", collection_method="scraper", object_type=endpoint, external_id=external,
            payload_hash="a" * 64, payload_json={"id": external, "name": "Observed song"}, observed_at=when)
        obj, _ = PlatformObject.objects.get_or_create(service=service, object_type=kind, external_id=external)
        definition, _ = MetricDefinition.objects.get_or_create(service=service, code=code, defaults={"unit": "count", "aggregation_rule": "cumulative", "period_type": "daily_snapshot"})
        dims = dimensions or {"scope": "public"}
        series, _ = MetricSeries.objects.get_or_create(organization=artist.organization, metric_definition=definition,
            platform_object=obj, dimensions_hash=dimension_hash(dims), source_method="omkar", defaults={"dimensions_json": dims})
        start = when.replace(hour=0)
        return MetricObservation.objects.create(series=series, source_record=source, value=value, period_start=start,
            period_end=start + timedelta(days=1), observed_at=when, revision=revision, quality_status=quality)

    def summary(self, user=None, portfolio=False):
        from portal.dashboard import dashboard_for
        return dashboard_for(user or self.user, self.artist, portfolio=portfolio)

    def test_latest_revision_alias_and_daily_snapshot_are_counted_once(self):
        self.observation(when=self.now - timedelta(days=1), value=50)
        self.observation(value=90)
        self.observation(value=100, revision=2)
        self.observation(code="plays", value=100)
        self.observation(kind="artist", external="profile", code="plays", value=9999)
        self.observation(value=99999, revision=3, quality="rejected")
        summary = self.summary()
        self.assertEqual(summary["headline"]["plays"]["value"], Decimal(100))
        self.assertEqual(summary["headline"]["plays"]["objects"], 1)

    def test_unknown_is_not_zero_and_dimensions_do_not_inflate_audience(self):
        self.observation(kind="artist", external="profile", code="followers", value=0)
        self.observation(kind="artist", external="profile", code="city_listeners", value=99, dimensions={"city": "Miami"})
        self.observation(kind="artist", external="profile", code="monthly_listeners", value=None)
        summary = self.summary()
        self.assertEqual(summary["headline"]["followers"]["value"], Decimal(0))
        self.assertIsNone(summary["headline"]["monthly_listeners"]["value"])
        self.assertIsNone(summary["headline"]["views"]["value"])

    def test_provider_limit_is_distinct_from_a_failed_collection(self):
        observation = self.observation()
        run = observation.source_record.sync_run
        run.status = "succeeded"
        run.counts_json = {"provider_limits": {"artist-discovered-on": {"returned": 40, "reported_total": 100}}}
        run.save()
        summary = self.summary()
        platform = next(p for p in summary["platforms"] if p["slug"] == "spotify")
        self.assertEqual(platform["state"], "Provider limited")
        self.assertFalse(summary["has_incomplete"])

    def test_list_post_and_enriched_post_do_not_double_count(self):
        first = self.observation(platform="instagram", kind="post", code="like_count", value=5)
        first.source_record.object_type = "post-list-item"
        first.source_record.save()
        self.observation(platform="instagram", kind="post", code="like_count", value=7, revision=2)
        platform = next(p for p in self.summary()["platforms"] if p["slug"] == "instagram")
        self.assertEqual(platform["metrics"]["likes"]["value"], Decimal(7))
        self.assertEqual(platform["items"], 1)

    def test_channel_total_is_kept_separate_from_video_views(self):
        self.observation(platform="youtube", kind="channel", external="channel", code="view_count", value=1000)
        self.observation(platform="youtube", kind="video", external="video", code="view_count", value=30)
        self.assertEqual(self.summary()["headline"]["views"]["value"], Decimal(30))
        youtube = next(p for p in self.summary()["platforms"] if p["slug"] == "youtube")
        self.assertEqual(youtube["metrics"]["channel_views"]["value"], Decimal(1000))

    def test_source_and_series_must_belong_to_the_selected_artist_and_organization(self):
        self.observation(value=100)
        self.observation(artist=self.other, external="hidden", value=9999)
        foreign = Organization.objects.create(name="Other org", slug="dashboard-foreign")
        corrupt = self.observation(external="corrupt", value=8888)
        MetricSeries.objects.filter(pk=corrupt.series_id).update(organization=foreign)
        self.assertEqual(self.summary()["headline"]["plays"]["value"], Decimal(100))
        self.assertEqual(self.summary(portfolio=True)["artist_count"], 1)

    def test_portfolio_counts_shared_platform_objects_once_and_excludes_collaborators(self):
        self.observation(value=100)
        self.observation(artist=self.other, value=200, revision=2)
        guest = Artist.objects.create(organization=self.org, name="Guest", slug="dashboard-guest", management_status="managed")
        self.observation(artist=guest, external="guest", value=9999)
        summary = self.summary(user=self.staff, portfolio=True)
        self.assertEqual(summary["artist_count"], 2)
        self.assertEqual(summary["headline"]["plays"]["value"], Decimal(200))
        self.assertTrue(summary["portfolio"])

    def test_follower_history_uses_latest_daily_revision_and_real_dates(self):
        self.observation(kind="artist", external="profile", code="followers", value=5, when=self.now - timedelta(days=3))
        self.observation(kind="artist", external="profile", code="followers", value=8)
        self.observation(kind="artist", external="profile", code="followers", value=10, revision=2)
        spotify = next(p for p in self.summary()["platforms"] if p["slug"] == "spotify")
        self.assertEqual([r["value"] for r in spotify["history"]], [Decimal(5), Decimal(10)])
        self.assertEqual(spotify["delta"], Decimal(5))
        self.assertTrue(spotify["points"])

    def test_overview_renders_dashboard_and_staff_portfolio_without_leaking_to_viewers(self):
        self.observation(value=100)
        self.observation(artist=self.other, value=200, revision=2)
        url = reverse("portal:overview")
        self.client.force_login(self.user)
        response = self.client.get(url, {"artist": self.artist.pk, "metrics_scope": "all"})
        self.assertContains(response, "Platform pulse")
        self.assertFalse(response.context["dashboard"]["portfolio"])
        self.assertNotContains(response, "All workspace artists")
        self.client.force_login(self.staff)
        response = self.client.get(url, {"artist": self.artist.pk, "metrics_scope": "all"})
        self.assertTrue(response.context["dashboard"]["portfolio"])
        self.assertContains(response, "All workspace artists")
        self.assertContains(response, "Incomplete")

    def test_empty_dashboard_stays_unavailable(self):
        summary = self.summary()
        self.assertFalse(summary["has_data"])
        self.assertTrue(all(v["value"] is None for v in summary["headline"].values()))

    def test_unrelated_source_cannot_supply_another_objects_metrics(self):
        row = self.observation(value=100)
        SourceRecord.objects.filter(pk=row.source_record_id).update(external_id="unrelated-track")
        self.assertIsNone(self.summary()["headline"]["plays"]["value"])

    def test_revision_numbers_from_different_metric_series_are_not_compared(self):
        self.observation(value=90)
        self.observation(value=100, revision=2)
        self.observation(code="plays", value=150, when=self.now + timedelta(hours=1))
        self.assertEqual(self.summary()["headline"]["plays"]["value"], Decimal(150))

    def test_summary_query_count_does_not_grow_with_the_number_of_songs(self):
        self.observation()
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as first:
            self.summary()
        for i in range(12):
            self.observation(external=f"extra-{i}")
        with CaptureQueriesContext(connection) as expanded:
            self.summary()
        self.assertEqual(len(expanded), len(first))
        self.assertLessEqual(len(expanded), 8)

