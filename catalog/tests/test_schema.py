from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from artists.models import Artist, ArtistMembership, Organization, Service
from catalog.models import Recording, RecordingArtist, RecordingRelation, RecordingIdentifier, Release, ReleaseTrack, PlatformObject, RecordingPlatformMatch, Asset, RecordingAsset
from rights.models import MusicalWork, RecordingWork, Party, RightsSociety, WorkRegistration, RightsClaim
from events.models import Event, EventSource, TicketOffer
from provenance.models import EntityTarget, FieldObservation, SourceRecord, ReviewItem, ReviewCandidate, EditorialOverride
from publishing.models import Website, Page, SEORecord, FanlinkPage, PublicationOutbox, WebsitePublication
from publishing.services import approve_page_and_request_publication
from analytics.models import MetricDefinition, MetricSeries, MetricObservation, ReportImport, AudioAnalysis, PlaylistAppearance, dimension_hash


class DatabaseFoundationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.other_org = Organization.objects.create(name="Other", slug="other")
        self.artist = Artist.objects.create(organization=self.org, name="Dezolent", slug="dezolent")
        self.other_artist = Artist.objects.create(organization=self.other_org, name="Guest", slug="guest")
        self.soundcloud = Service.objects.create(name="SoundCloud", slug="soundcloud")

    def test_soundcloud_only_recording(self):
        recording = Recording.objects.create(organization=self.org, title="Demo", date_precision="unknown")
        obj = PlatformObject.objects.create(service=self.soundcloud, object_type="track", url="https://soundcloud.com/example/demo")
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj)
        self.assertFalse(ReleaseTrack.objects.filter(recording=recording).exists())
        self.assertFalse(RecordingWork.objects.filter(recording=recording).exists())
        self.assertEqual(recording.identifiers.count(), 0)

    def test_one_recording_on_two_releases_and_remix_distinct(self):
        original = Recording.objects.create(organization=self.org, title="Demo")
        remix = Recording.objects.create(organization=self.org, title="Demo", version_title="Remix", recording_type="remix")
        for n in (1, 2):
            release = Release.objects.create(organization=self.org, title=f"Product {n}")
            ReleaseTrack.objects.create(release=release, recording=original, track_number=1)
        RecordingRelation.objects.create(from_recording=remix, to_recording=original, relation_type="remix_of")
        self.assertEqual(original.release_tracks.count(), 2)
        self.assertNotEqual(remix.pk, original.pk)

    def test_collaboration_credit_does_not_grant_artist_access(self):
        user = get_user_model().objects.create_user(username="guest-user", password="test-only-password")
        ArtistMembership.objects.create(artist=self.other_artist, user=user, role="viewer")
        recording = Recording.objects.create(organization=self.org, title="Collab")
        RecordingArtist.objects.create(recording=recording, artist=self.other_artist)
        self.client.force_login(user)
        self.assertEqual(self.client.get(f"/api/v1/artists/{self.artist.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/artists/{self.other_artist.pk}/").status_code, 200)

    def test_many_to_many_recording_works(self):
        recording = Recording.objects.create(organization=self.org, title="Medley")
        for title in ("Work A", "Work B"):
            work = MusicalWork.objects.create(organization=self.org, title=title)
            RecordingWork.objects.create(recording=recording, work=work)
        self.assertEqual(RecordingWork.objects.filter(recording=recording).count(), 2)

    def test_platform_dedup_and_url_only(self):
        PlatformObject.objects.create(service=self.soundcloud, object_type="track", external_id="0123", url="https://soundcloud.com/a")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlatformObject.objects.create(service=self.soundcloud, object_type="track", external_id="0123", url="https://soundcloud.com/b")
        PlatformObject.objects.create(service=self.soundcloud, object_type="track", url="https://soundcloud.com/url-only")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlatformObject.objects.create(service=self.soundcloud, object_type="track", url="https://soundcloud.com/url-only")

    def test_identifier_assertions_and_track_positions(self):
        a = Recording.objects.create(organization=self.org, title="Version A")
        b = Recording.objects.create(organization=self.org, title="Version B")
        RecordingIdentifier.objects.create(recording=a, scheme="ISRC", value="ASSERTION-1")
        RecordingIdentifier.objects.create(recording=b, scheme="ISRC", value="ASSERTION-1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecordingIdentifier.objects.create(recording=a, scheme="ISRC", value="ASSERTION-1")
        release = Release.objects.create(organization=self.org, title="Single")
        ReleaseTrack.objects.create(release=release, recording=a, track_number=1)
        with self.assertRaises(ValidationError):
            ReleaseTrack.objects.create(release=release, recording=b, track_number=1)

    def test_private_assets_and_release_tracks_keep_ownership(self):
        recording = Recording.objects.create(organization=self.org, title="Owned song")
        foreign_release = Release.objects.create(organization=self.other_org, title="Foreign album")
        with self.assertRaises(ValidationError):
            ReleaseTrack.objects.create(release=foreign_release, recording=recording, track_number=1)
        foreign_asset = Asset.objects.create(organization=self.other_org, source_url="https://example.test/art.webp")
        with self.assertRaises(ValidationError):
            RecordingAsset.objects.create(recording=recording, asset=foreign_asset, purpose="cover")
        with self.assertRaises(ValidationError):
            ReportImport.objects.create(artist=self.artist, service=self.soundcloud, asset=foreign_asset, file_hash="a" * 64, report_type="streams")

    def test_publication_and_analysis_assets_keep_ownership(self):
        foreign_asset = Asset.objects.create(organization=self.other_org, source_url="https://example.test/private.json")
        website = Website.objects.create(organization=self.org, artist=self.artist, domain="assets.test")
        page = Page.objects.create(website=website, path="/", page_type="home", title="Home")
        with self.assertRaises(ValidationError):
            SEORecord.objects.create(page=page, og_asset=foreign_asset)
        with self.assertRaises(ValidationError):
            WebsitePublication.objects.create(website=website, snapshot_asset=foreign_asset, content_version=1)
        recording = Recording.objects.create(organization=self.org, title="Demo")
        with self.assertRaises(ValidationError):
            AudioAnalysis.objects.create(recording=recording, input_asset=foreign_asset, engine="test", engine_version="1", analyzed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_fanlink_exactly_one_target(self):
        website = Website.objects.create(organization=self.org, artist=self.artist, domain="example.test")
        recording = Recording.objects.create(organization=self.org, title="Song")
        release = Release.objects.create(organization=self.org, title="Album")
        with self.assertRaises(ValidationError):
            FanlinkPage.objects.create(website=website, recording=recording, release=release, slug="both", title="Both")
        with self.assertRaises(ValidationError):
            FanlinkPage.objects.create(website=website, slug="neither", title="Neither")

    def test_fanlink_rejects_foreign_organization(self):
        website = Website.objects.create(organization=self.org, artist=self.artist, domain="owned.test")
        foreign_recording = Recording.objects.create(organization=self.other_org, title="Guest song")
        with self.assertRaises(ValidationError):
            FanlinkPage.objects.create(website=website, recording=foreign_recording, slug="guest", title="Guest")
        with self.assertRaises(ValidationError):
            Website.objects.create(organization=self.org, artist=self.other_artist, domain="foreign.test")

    def test_source_specific_rights_claims_without_total_assumption(self):
        work = MusicalWork.objects.create(organization=self.org, title="Composition")
        society = RightsSociety.objects.create(name="Test Society", code="TEST", society_type="performance")
        registration = WorkRegistration.objects.create(work=work, society=society, registration_status="not_found")
        party = Party.objects.create(organization=self.org, party_type="person", display_name="Writer")
        a = RightsClaim.objects.create(registration=registration, party=party, right_type="performance", share_value=Decimal("50"), share_basis="writer", territory="US")
        b = RightsClaim.objects.create(registration=registration, party=party, right_type="mechanical", share_value=Decimal("75"), share_basis="publisher", territory="CA")
        self.assertNotEqual(a.share_basis, b.share_basis)
        self.assertNotEqual(registration.registration_status, "confirmed_unregistered")

    def test_date_only_event_and_offer_source_integrity(self):
        event = Event.objects.create(organization=self.org, title="Show", local_date=date(2027, 1, 2), status="postponed")
        other = Event.objects.create(organization=self.org, title="Other", local_date=date(2027, 1, 3))
        source = EventSource.objects.create(event=other, service=self.soundcloud, external_id="123")
        self.assertIsNone(event.starts_at)
        with self.assertRaises(ValidationError):
            TicketOffer(event=event, event_source=source, url="https://example.test/tickets").full_clean()

    def test_typed_provenance_rejects_cross_org_target(self):
        target = EntityTarget(organization=self.org, artist=self.other_artist)
        with self.assertRaises(ValidationError): target.full_clean()
        with self.assertRaises(ValidationError):
            EntityTarget.objects.create(organization=self.org)

    def test_review_candidates_and_fields_are_tenant_scoped(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        source = SourceRecord.objects.create(organization=self.org, collector="manual", collection_method="manual", object_type="artist", payload_hash="a" * 64, payload_json={}, observed_at=now)
        own_target = EntityTarget.objects.create(organization=self.org, artist=self.artist)
        other_target = EntityTarget.objects.create(organization=self.other_org, artist=self.other_artist)
        review = ReviewItem.objects.create(source_record=source, issue_type="ambiguous")
        ReviewCandidate.objects.create(review_item=review, target=own_target)
        with self.assertRaises(ValidationError):
            ReviewCandidate.objects.create(review_item=review, target=other_target)
        with self.assertRaises(ValidationError):
            FieldObservation.objects.create(source_record=source, target=own_target, field_name="legal_name", value_json="hidden")
        with self.assertRaises(ValidationError):
            SourceRecord.objects.create(organization=self.org, collector="manual", collection_method="manual", object_type="artist", payload_hash="b" * 64, payload_json={"nested": {"access_token": "placeholder"}}, observed_at=now)

    def test_metric_dimensions_and_accepted_revision(self):
        definition = MetricDefinition.objects.create(service=self.soundcloud, code="plays", unit="plays", aggregation_rule="cumulative", period_type="day")
        obj = PlatformObject.objects.create(service=self.soundcloud, object_type="track", external_id="plays-1")
        series = MetricSeries.objects.create(organization=self.org, metric_definition=definition, platform_object=obj, dimensions_json={"territory": "US", "device": "mobile"}, source_method="public_api")
        self.assertEqual(series.dimensions_hash, dimension_hash({"device":"mobile", "territory":"US"}))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)
        for revision, value, quality in ((1, 100, "accepted"), (2, None, "rejected"), (3, 110, "accepted")):
            MetricObservation.objects.create(series=series, period_start=start, period_end=end, observed_at=end, revision=revision, value=value, quality_status=quality)
        self.assertEqual(MetricObservation.current_accepted(series, start, end).value, 110)

    def test_metric_service_and_playlist_source_ownership(self):
        other_service = Service.objects.create(name="Other", slug="other")
        definition = MetricDefinition.objects.create(service=self.soundcloud, code="plays", unit="plays", aggregation_rule="cumulative", period_type="day")
        obj = PlatformObject.objects.create(service=other_service, object_type="track", external_id="other-1")
        with self.assertRaises(ValidationError):
            MetricSeries.objects.create(organization=self.org, metric_definition=definition, platform_object=obj, source_method="api")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        source = SourceRecord.objects.create(organization=self.other_org, collector="manual", collection_method="manual", object_type="playlist", payload_hash="c" * 64, payload_json={}, observed_at=now)
        recording = Recording.objects.create(organization=self.org, title="Song")
        with self.assertRaises(ValidationError):
            PlaylistAppearance.objects.create(recording=recording, playlist_object=obj, first_observed_at=now, last_observed_at=now, source_record=source)

    def test_publication_request_idempotency(self):
        website = Website.objects.create(organization=self.org, artist=self.artist, domain="publish.test")
        page = Page.objects.create(website=website, path="/", page_type="home", title="Home")
        a = approve_page_and_request_publication(page.pk, "page-home-v1")
        b = approve_page_and_request_publication(page.pk, "page-home-v1")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(PublicationOutbox.objects.count(), 1)
        page.refresh_from_db()
        self.assertEqual(page.publication_status, "approved")

    def test_staff_admin_and_artist_api_boundaries(self):
        user = get_user_model().objects.create_user(username="catalog-member", password="test-only-password")
        ArtistMembership.objects.create(artist=self.artist, user=user, role="viewer")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/catalog/recording/").status_code, 302)
        self.assertEqual(self.client.get(f"/api/v1/artists/{self.other_artist.pk}/").status_code, 404)
        self.client.logout()
        admin_user = get_user_model().objects.create_superuser(username="catalog-admin", email="admin@example.test", password="test-only-password")
        self.client.force_login(admin_user)
        self.assertEqual(self.client.get("/admin/catalog/recording/").status_code, 200)
        self.assertEqual(self.client.get("/admin/rights/musicalwork/").status_code, 200)

    def test_existing_seed_and_reference_seed_preserve_edits(self):
        call_command("seed_artists", stdout=StringIO())
        self.artist.refresh_from_db()
        self.assertEqual(Artist.objects.count(), 4)
        self.assertEqual(self.artist.organization_id, self.org.pk)
        call_command("seed_references", stdout=StringIO())
        service = Service.objects.get(slug="spotify")
        service.name = "Edited Spotify"
        service.save()
        call_command("seed_references", stdout=StringIO())
        self.assertEqual(Service.objects.get(slug="spotify").name, "Edited Spotify")
        society = RightsSociety.objects.get(code="MLC")
        society.name = "Edited MLC"
        society.save()
        call_command("seed_references", stdout=StringIO())
        self.assertEqual(RightsSociety.objects.get(code="MLC").name, "Edited MLC")
