"""Accepted catalog identities connect private observations without title matching."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import MetricDefinition, MetricObservation, MetricSeries
from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.models import OperationRemoval
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class RecordingMetricsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Metrics artist", slug="recording-metrics", management_status="managed")
        self.other = Artist.objects.create(organization=self.org, name="Other metrics artist", slug="other-metrics", management_status="managed")
        self.user = get_user_model().objects.create_user(username="recording-metrics")
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        self.client.force_login(self.user)
        self.song = Recording.objects.create(organization=self.org, title="Catalog song")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song)
        self.spotify = Service.objects.create(name="Spotify", slug="spotify")
        self.obj = PlatformObject.objects.create(service=self.spotify, object_type="track", external_id="track-one")
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.obj, review_status="accepted")
        self.source = self.make_source(self.spotify, "track-one", "track-details")
        self.definition = MetricDefinition.objects.create(service=self.spotify, code="playcount", unit="count", aggregation_rule="cumulative", period_type="daily_snapshot")
        self.series = MetricSeries.objects.create(organization=self.org, platform_object=self.obj, metric_definition=self.definition, source_method="omkar", dimensions_json={"scope": "public"})

    def make_source(self, service, external_id, endpoint, *, artist=None, collector="omkar", payload=None):
        artist = artist or self.artist
        connection, _ = Connection.objects.get_or_create(organization=artist.organization, artist=artist, service=service)
        job, _ = SyncJob.objects.get_or_create(connection=connection, job_type="daily_public_platform")
        run = SyncRun.objects.create(sync_job=job, status="succeeded")
        return SourceRecord.objects.create(organization=artist.organization, service=service, sync_run=run,
            collector=collector, collection_method="scraper", object_type=endpoint, external_id=external_id,
            payload_hash="a" * 64, observed_at=timezone.now(), payload_json=payload or {"name": "Observed song", "link": f"https://open.spotify.com/track/{external_id}"})

    def observation(self, value, *, days=0, revision=1, status="pending", source=None, series=None):
        observed = timezone.now() - timedelta(days=days)
        start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        return MetricObservation.objects.create(series=series or self.series, source_record=source or self.source,
            observed_at=observed, period_start=start, period_end=start + timedelta(days=1), value=value,
            revision=revision, quality_status=status)

    def groups(self):
        from portal.recording_metrics import recording_metrics
        return recording_metrics(self.artist, self.song)

    def test_legacy_url_match_resolves_canonical_observations_and_zero(self):
        legacy = PlatformObject.objects.create(service=self.spotify, object_type="recording", url="https://open.spotify.com/track/track-one?si=tracking")
        self.match.platform_object = legacy
        self.match.save()
        self.observation(0)
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["metrics"][0]["latest"].value, Decimal(0))
        response = self.client.get(reverse("portal:recording", args=[self.artist.pk, self.song.pk]))
        self.assertContains(response, "Audience &amp; engagement")
        self.assertContains(response, f"recording={self.song.pk}")
        self.assertContains(response, "Playcount")
        self.assertContains(response, reverse("portal:insight-detail", args=[self.artist.pk, self.source.pk]))

    def test_only_active_accepted_granted_matches_attach(self):
        self.observation(88)
        for status in ("pending", "rejected"):
            self.match.review_status = status
            self.match.save()
            self.assertEqual(self.groups(), [])
        self.match.review_status = "accepted"
        self.match.save()
        removal = OperationRemoval.objects.create(model_label="catalog.recordingplatformmatch", object_id=str(self.match.pk), organization=self.org)
        self.assertEqual(self.groups(), [])
        removal.delete()
        self.song.archived_at = timezone.now()
        self.song.save()
        self.assertEqual(self.groups(), [])
        self.song.archived_at = None
        self.song.save()
        ArtistResourceGrant.objects.filter(artist=self.artist, recording=self.song).delete()
        self.assertEqual(self.groups(), [])

    def test_source_artist_and_series_organization_are_both_required(self):
        foreign = self.make_source(self.spotify, "track-one", "track-details", artist=self.other)
        self.observation(99999, source=foreign)
        org = Organization.objects.create(name="Other organization", slug="other-metrics-org")
        foreign_series = MetricSeries.objects.create(organization=org, platform_object=self.obj, metric_definition=self.definition, source_method="omkar", dimensions_json={"scope": "public"})
        # Simulate corrupt legacy data to exercise the read boundary independently of model validation.
        row = self.observation(88888, revision=2)
        MetricObservation.objects.filter(pk=row.pk).update(series=foreign_series)
        self.assertEqual(self.groups()[0]["metrics"], [])

    def test_duplicate_collectors_choose_latest_and_revisions_do_not_sum(self):
        official = self.make_source(self.spotify, "track-one", "spotify-track", collector="spotify-web-api")
        official_series = MetricSeries.objects.create(organization=self.org, platform_object=self.obj, metric_definition=self.definition, source_method="spotify-web-api", dimensions_json={"scope": "public"})
        self.observation(10, days=2)
        self.observation(11, days=2, revision=2)
        self.observation(20, days=1)
        self.observation(999, days=0, status="rejected")
        self.observation(30, source=official, series=official_series)
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        metrics = groups[0]["metrics"]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["latest"].value, Decimal(30))
        self.assertEqual([row.value for row in metrics[0]["history"]], [Decimal(11), Decimal(20), Decimal(30)])
        self.assertEqual(metrics[0]["delta"], Decimal(10))
        response = self.client.get(reverse("portal:insights", args=[self.artist.pk]), {"recording": self.song.pk})
        self.assertEqual(len(response.context["cards"]), 1)
        self.assertEqual(response.context["cards"][0]["metrics"][0]["latest"].value, Decimal(30))

    def test_missing_values_remain_unknown(self):
        self.observation(None)
        response = self.client.get(reverse("portal:recording", args=[self.artist.pk, self.song.pk]))
        self.assertContains(response, "Unavailable")
        self.assertIsNone(self.groups()[0]["metrics"][0]["latest"].value)

    def test_recording_filter_is_uuid_scoped_and_links_back_to_catalog(self):
        url = reverse("portal:insights", args=[self.artist.pk])
        self.assertEqual(self.client.get(url, {"recording": "not-a-uuid"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"recording": uuid4()}).status_code, 404)
        ungranted = Recording.objects.create(organization=self.org, title="Other song")
        self.assertEqual(self.client.get(url, {"recording": ungranted.pk}).status_code, 404)
        response = self.client.get(url, {"recording": self.song.pk})
        self.assertContains(response, "Observed song")
        self.assertContains(response, reverse("portal:recording", args=[self.artist.pk, self.song.pk]))
        self.match.review_status = "pending"
        self.match.save()
        self.assertNotContains(self.client.get(url, {"recording": self.song.pk}), "Observed song")

    def test_soundcloud_permalink_resolves_numeric_metric_identity(self):
        service = Service.objects.create(name="SoundCloud", slug="soundcloud")
        canonical = PlatformObject.objects.create(service=service, object_type="track", external_id="123456")
        legacy = PlatformObject.objects.create(service=service, object_type="recording", url="https://soundcloud.com/artist/song?utm_source=share")
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=legacy, review_status="accepted")
        source = self.make_source(service, "123456", "track-details", payload={"name": "SoundCloud song", "link": "https://soundcloud.com/artist/song"})
        definition = MetricDefinition.objects.create(service=service, code="plays", unit="count", aggregation_rule="cumulative", period_type="daily_snapshot")
        series = MetricSeries.objects.create(organization=self.org, platform_object=canonical, metric_definition=definition, source_method="omkar", dimensions_json={"scope": "public"})
        self.observation(123, source=source, series=series)
        self.assertEqual(next(g for g in self.groups() if g["source"].service == service)["metrics"][0]["latest"].value, Decimal(123))

    def test_album_url_never_attaches_song_metrics(self):
        self.match.platform_object = PlatformObject.objects.create(service=self.spotify, object_type="recording", url="https://open.spotify.com/album/track-one")
        self.match.save()
        self.observation(56)
        self.assertEqual(self.groups(), [])

    def test_metric_provenance_must_match_the_observed_object_identity(self):
        unrelated = self.make_source(self.spotify, "other-track", "track-details")
        self.observation(444, source=unrelated)
        self.assertEqual(self.groups()[0]["metrics"], [])

    def test_empty_recording_filter_is_rejected(self):
        self.assertEqual(self.client.get(reverse("portal:insights", args=[self.artist.pk]), {"recording": ""}).status_code, 404)

    def test_batch_metrics_uses_two_queries_for_current_and_history(self):
        from portal.insights import metric_cards_for_sources
        self.observation(4)
        extra = self.make_source(self.spotify, "another-track", "track-details")
        with self.assertNumQueries(2):
            metrics = metric_cards_for_sources(self.artist, [self.source, extra], history=True)
        self.assertEqual(len(metrics[(self.spotify.pk, "track", "track-one")]), 1)
        self.assertEqual(metrics[(self.spotify.pk, "track", "another-track")], [])

    def test_apple_song_query_and_youtube_watch_resolve_without_album_matches(self):
        cases = [
            ("apple-music", "Apple Music", "https://music.apple.com/us/album/name/123?i=456", "456", "song-details", "track"),
            ("youtube", "YouTube", "https://www.youtube.com/watch?v=abc123&feature=share", "abc123", "video-details", "video"),
        ]
        for slug, name, url, external_id, endpoint, kind in cases:
            service = Service.objects.create(slug=slug, name=name)
            canonical = PlatformObject.objects.create(service=service, object_type=kind, external_id=external_id)
            legacy = PlatformObject.objects.create(service=service, object_type="recording", url=url)
            RecordingPlatformMatch.objects.create(recording=self.song, platform_object=legacy, review_status="accepted")
            source = self.make_source(service, external_id, endpoint)
            definition = MetricDefinition.objects.create(service=service, code="views", unit="count", aggregation_rule="cumulative", period_type="daily_snapshot")
            series = MetricSeries.objects.create(organization=self.org, platform_object=canonical, metric_definition=definition, source_method="omkar", dimensions_json={"scope": "public"})
            self.observation(7, source=source, series=series)
            self.assertEqual(next(group for group in self.groups() if group["source"].service_id == service.pk)["metrics"][0]["latest"].value, Decimal(7))

    def test_invalid_legacy_url_does_not_break_an_explicit_platform_id(self):
        PlatformObject.objects.filter(pk=self.obj.pk).update(url="https://[")
        self.observation(9)
        self.assertEqual(self.groups()[0]["metrics"][0]["latest"].value, Decimal(9))

    def test_song_view_has_one_metadata_view_and_no_collector_branding(self):
        self.source.payload_json.update(language='English', duration_ms=180000, explicit=False, genres=['Pop'])
        self.source.save()
        self.song.language = 'French'
        self.song.save()
        self.observation(0)
        response = self.client.get(reverse('portal:recording', args=[self.artist.pk, self.song.pk]))
        self.assertNotContains(response, 'omkar')
        self.assertNotContains(response, '<details class="catalog-section"')
        self.assertNotContains(response, 'Platform-reported metadata')
        self.assertContains(response, 'French')
        self.assertNotContains(response, 'English')
        self.assertContains(response, '3:00')
        self.assertContains(response, 'Audio features')

    def test_tiktok_distinct_sounds_sum_once_and_aliases_are_deduplicated(self):
        service = Service.objects.create(name='TikTok', slug='tiktok')
        definition = MetricDefinition.objects.create(service=service, code='video_count', unit='count', aggregation_rule='cumulative', period_type='daily_snapshot')
        for identity, value in [('123', 4), ('456', 6)]:
            obj = PlatformObject.objects.create(service=service, object_type='music', external_id=identity)
            RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj, review_status='accepted')
            source = self.make_source(service, identity, 'tiktok-music')
            series = MetricSeries.objects.create(organization=self.org, platform_object=obj, metric_definition=definition, source_method='omkar', dimensions_json={'scope':'public'})
            self.observation(value, source=source, series=series)
            self.observation(value + 1, source=source, series=series, revision=2)
        alias = PlatformObject.objects.create(service=service, object_type='recording', url='https://www.tiktok.com/music/song-123')
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=alias, review_status='accepted')
        group = next(g for g in self.groups() if g['source'].service == service)
        self.assertEqual(len(group['metrics']), 1)
        self.assertEqual(group['metrics'][0]['label'], 'Videos using this song')
        self.assertEqual(group['metrics'][0]['value'], Decimal(12))
        self.assertEqual(group['metrics'][0]['sound_count'], 2)
