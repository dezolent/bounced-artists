from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from artists.models import Artist, ArtistMembership, Organization, Service
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class InsightsTests(TestCase):
    def setUp(self):
        org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=org, name="Insights Artist", slug="insights", management_status="managed")
        self.other = Artist.objects.create(organization=org, name="Other Artist", slug="other-insights", management_status="managed")
        self.user = get_user_model().objects.create_user(username="insights-viewer")
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        service = Service.objects.create(name="Spotify", slug="spotify")
        conn = Connection.objects.create(organization=org, artist=self.artist, service=service, status="active")
        self.job = SyncJob.objects.create(connection=conn, job_type="daily_public_platform", enabled=True)
        run = SyncRun.objects.create(sync_job=self.job, status="succeeded", started_at=timezone.now(), finished_at=timezone.now())
        self.source = SourceRecord.objects.create(organization=org, service=service, sync_run=run, collector="omkar", collection_method="scraper", object_type="track-details", external_id="track-1", payload_hash="a" * 64, observed_at=timezone.now(), payload_json={
            "id": "track-1", "name": "Observed song", "duration_ms": 123000,
            "playcount": 0, "preview_link": "javascript:alert(1)",
            "link": "https://open.spotify.com/track/track-1",
            "album": {"id": "album-1", "name": "Observed release", "release_date": "2026-10-01", "images": []},
            "credits": {"contributors": [{"name": "Song Writer", "role": "Composer"}]},
            "private_internal_field": "DO-NOT-DISPLAY",
        })
        self.client.force_login(self.user)

    def test_viewer_sees_release_data_and_zero_without_raw_private_fields(self):
        response = self.client.get(reverse("portal:insights", args=[self.artist.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Observed song")
        self.assertContains(response, "Observed release")
        response = self.client.get(reverse("portal:insight-detail", args=[self.artist.pk, self.source.pk]))
        self.assertContains(response, "Composer")
        self.assertContains(response, "Song Writer")
        self.assertContains(response, "2026-10-01")
        self.assertNotContains(response, "DO-NOT-DISPLAY")
        self.assertNotContains(response, "javascript:")

    def test_artist_and_source_lookups_do_not_cross_workspaces(self):
        self.assertEqual(self.client.get(reverse("portal:insights", args=[self.other.pk])).status_code, 404)
        conn = Connection.objects.create(organization=self.other.organization, artist=self.other, service=self.job.connection.service)
        other_job = SyncJob.objects.create(connection=conn, job_type="daily_public_platform")
        other_run = SyncRun.objects.create(sync_job=other_job)
        self.source.sync_run = other_run
        self.source.save()
        self.assertEqual(self.client.get(reverse("portal:insight-detail", args=[self.artist.pk, self.source.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("portal:insights", args=[self.artist.pk])), "Observed song")

    def test_empty_view_explains_unavailable_data_without_fabricating_metrics(self):
        SourceRecord.objects.all().delete()
        response = self.client.get(reverse("portal:insights", args=[self.artist.pk]))
        self.assertContains(response, "No observations yet")
        self.assertNotContains(response, "Observed song")

    def test_official_identifiers_and_apple_editorial_descriptions_are_visible(self):
        self.source.object_type = "spotify-track"
        self.source.collector = "spotify-web-api"
        self.source.payload_json = {"id": "track-1", "name": "Official song", "external_ids": {"isrc": "USAAA2600001"}, "description": {"short": "Public editorial description"}}
        self.source.save()
        response = self.client.get(reverse("portal:insight-detail", args=[self.artist.pk, self.source.pk]))
        self.assertContains(response, "USAAA2600001")
        self.assertContains(response, "Public editorial description")

    def test_release_filter_resolves_existing_url_only_platform_matches(self):
        from artists.models import ArtistResourceGrant
        from catalog.models import PlatformObject, Recording, RecordingPlatformMatch, Release, ReleaseTrack
        recording = Recording.objects.create(organization=self.artist.organization, title="Catalog song")
        release = Release.objects.create(organization=self.artist.organization, title="Catalog release")
        ReleaseTrack.objects.create(release=release, recording=recording, track_number=1)
        ArtistResourceGrant.objects.create(artist=self.artist, release=release)
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        obj = PlatformObject.objects.create(service=self.source.service, object_type="recording", url="https://open.spotify.com/track/track-1?si=tracking")
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj, review_status="accepted")
        response = self.client.get(reverse("portal:insights", args=[self.artist.pk]), {"release": str(release.pk)})
        self.assertContains(response, "Observed song")
