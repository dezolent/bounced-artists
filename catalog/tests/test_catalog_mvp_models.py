from datetime import date
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from artists.models import Artist, ArtistResourceGrant, Organization
from catalog import models as catalog
from rights import models as rights


class CatalogMVPModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Catalog model tests", slug="catalog-model-tests")
        self.other_org = Organization.objects.create(name="Other catalog", slug="other-catalog-model-tests")
        self.artist = Artist.objects.create(organization=self.org, name="Artist", slug="model-test-artist")
        self.recording = catalog.Recording.objects.create(organization=self.org, title="Song")
        self.release = catalog.Release.objects.create(organization=self.org, title="Edition")

    def test_editions_share_explicit_group_without_replacing_release_identity(self):
        group = catalog.ReleaseGroup.objects.create(organization=self.org, title="Album", primary_type="album", secondary_types=["live"], first_release_date=date(2020, 1, 1), date_precision="year")
        original_id = self.release.pk
        self.release.release_group = group
        self.release.edition = "Deluxe"
        self.release.save()
        catalog.Release.objects.create(organization=self.org, title="Album", release_group=group)
        self.assertEqual(group.releases.count(), 2)
        self.release.refresh_from_db()
        self.assertEqual(self.release.pk, original_id)
        self.assertEqual(self.release.edition, "Deluxe")

    def test_release_rejects_foreign_release_group(self):
        group = catalog.ReleaseGroup.objects.create(organization=self.other_org, title="Foreign")
        self.release.release_group = group
        with self.assertRaises(ValidationError):
            self.release.save()

    def test_group_secondary_types_are_bounded_strings(self):
        for value in ({}, {"live": True}, ["x" * 31], ["live"] * 11, [42]):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                catalog.ReleaseGroup.objects.create(organization=self.org, title="Album", secondary_types=value)

    def test_label_name_does_not_convert_legacy_party(self):
        party = rights.Party.objects.create(organization=self.org, party_type="label", display_name="Legacy label", legal_name="Private legal entity")
        self.release.label_party = party
        self.release.save()
        self.assertEqual(catalog.Label.objects.count(), 0)
        label = catalog.Label.objects.create(organization=self.org, name="Public imprint", party=party)
        link = catalog.ReleaseLabel.objects.create(release=self.release, label=label, catalog_number="BS-001", is_primary=True)
        self.assertEqual(str(link), "Edition — Public imprint")
        self.release.refresh_from_db()
        self.assertEqual(self.release.label_party_id, party.pk)

    def test_label_rejects_foreign_private_party(self):
        party = rights.Party.objects.create(organization=self.other_org, party_type="label", display_name="Foreign")
        with self.assertRaises(ValidationError):
            catalog.Label.objects.create(organization=self.org, name="Imprint", party=party)

    def test_recording_and_release_labels_are_independent(self):
        first = catalog.Label.objects.create(organization=self.org, name="Song label")
        second = catalog.Label.objects.create(organization=self.org, name="Release label")
        catalog.RecordingLabel.objects.create(recording=self.recording, label=first, is_primary=True)
        catalog.ReleaseLabel.objects.create(release=self.release, label=second, is_primary=True)
        self.assertEqual(self.recording.labels.get().label, first)
        self.assertEqual(self.release.labels.get().label, second)

    def test_label_links_reject_foreign_organizations_and_duplicate_primary(self):
        foreign = catalog.Label.objects.create(organization=self.other_org, name="Foreign")
        own = catalog.Label.objects.create(organization=self.org, name="Own")
        second = catalog.Label.objects.create(organization=self.org, name="Second")
        for model, field, parent in ((catalog.RecordingLabel, "recording", self.recording), (catalog.ReleaseLabel, "release", self.release)):
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.objects.create(**{field: parent, "label": foreign})
                model.objects.create(**{field: parent, "label": own, "is_primary": True})
                with self.assertRaises(ValidationError):
                    model.objects.create(**{field: parent, "label": second, "is_primary": True})

    def test_label_and_group_grants_require_one_same_organization_target(self):
        label = catalog.Label.objects.create(organization=self.org, name="Label")
        group = catalog.ReleaseGroup.objects.create(organization=self.org, title="Group")
        foreign = catalog.Label.objects.create(organization=self.other_org, name="Foreign")
        ArtistResourceGrant.objects.create(artist=self.artist, label=label, capability="manage")
        ArtistResourceGrant.objects.create(artist=self.artist, release_group=group, capability="read")
        with self.assertRaises(ValidationError):
            ArtistResourceGrant.objects.create(artist=self.artist, label=foreign)
        with self.assertRaises(ValidationError):
            ArtistResourceGrant.objects.create(artist=self.artist, label=label, release_group=group)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtistResourceGrant.objects.bulk_create([ArtistResourceGrant(artist=self.artist, label=label, release=self.release)])
        self.assertEqual(self.artist.resource_grants.count(), 2)

    def test_medium_requires_positive_unique_position_and_track_matches_medium(self):
        medium = catalog.Medium.objects.create(release=self.release, position=1, format="Digital Media")
        other_release = catalog.Release.objects.create(organization=self.org, title="Other")
        other_medium = catalog.Medium.objects.create(release=other_release, position=1)
        with self.assertRaises(ValidationError):
            catalog.Medium.objects.create(release=self.release, position=0)
        with self.assertRaises(ValidationError):
            catalog.Medium.objects.create(release=self.release, position=1)
        for medium_value, disc in ((other_medium, 1), (medium, 2)):
            with self.assertRaises(ValidationError):
                catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, medium=medium_value, disc_number=disc, track_number=1)
        track = catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, medium=medium, track_number=1, duration_ms=120000, credit_text="Artist feat. Guest", mbid=uuid4())
        self.assertEqual(track.disc_number, 1)
        with self.assertRaises(ValidationError):
            medium.position = 2
            medium.save()

    def test_detaching_medium_and_track_keeps_song(self):
        medium = catalog.Medium.objects.create(release=self.release, position=1)
        track = catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, medium=medium, track_number=1)
        medium.delete()
        track.refresh_from_db()
        self.assertIsNone(track.medium_id)
        track.delete()
        self.assertTrue(catalog.Recording.objects.filter(pk=self.recording.pk).exists())

    def test_explicit_release_genres_do_not_change_track_genres(self):
        genre = catalog.Genre.objects.create(name="Electronic", slug="model-electronic")
        group = catalog.ReleaseGroup.objects.create(organization=self.org, title="Group")
        catalog.ReleaseGenre.objects.create(release=self.release, genre=genre)
        catalog.ReleaseGroupGenre.objects.create(release_group=group, genre=genre)
        self.assertFalse(catalog.RecordingGenre.objects.filter(recording=self.recording).exists())
        self.assertEqual(self.release.genres.get().genre, genre)

    def test_work_identifiers_preserve_legacy_iswc_and_do_not_merge_work(self):
        work = rights.MusicalWork.objects.create(organization=self.org, title="Work", iswc="T-legacy")
        other = rights.MusicalWork.objects.create(organization=self.org, title="Work")
        rights.WorkIdentifier.objects.create(work=work, scheme="iswc", value="T-123")
        rights.WorkIdentifier.objects.create(work=work, scheme="iswc", value="T-456")
        rights.WorkIdentifier.objects.create(work=other, scheme="iswc", value="T-123")
        work.refresh_from_db()
        self.assertEqual(work.iswc, "T-legacy")
        self.assertEqual(work.identifiers.count(), 2)
        self.assertNotEqual(work.pk, other.pk)

    def test_credit_display_names_and_join_phrases_preserve_credit_identity(self):
        for model, field, parent in ((catalog.RecordingArtist, "recording", self.recording), (catalog.ReleaseArtist, "release", self.release)):
            credit = model.objects.create(**{field: parent}, artist=self.artist, credited_as="Stage name", join_phrase=" feat. ")
            original_id = credit.pk
            credit.credited_as = "Another credited name"
            credit.save()
            credit.refresh_from_db()
            self.assertEqual(credit.pk, original_id)
            self.assertEqual(credit.join_phrase, " feat. ")

    def test_work_identifier_rejects_foreign_evidence(self):
        from django.utils import timezone
        from provenance.models import SourceRecord
        work = rights.MusicalWork.objects.create(organization=self.org, title="Work")
        source = SourceRecord.objects.create(organization=self.other_org, collector="manual", collection_method="manual", object_type="work", payload_hash="a" * 64, payload_json={}, observed_at=timezone.now())
        with self.assertRaises(ValidationError):
            rights.WorkIdentifier.objects.create(work=work, scheme="iswc", value="T-123", source_record=source)

    def test_catalog_notes_are_bounded_without_truncation(self):
        work = rights.MusicalWork(organization=self.org, title="Work")
        for record in (self.recording, self.release, work):
            record.notes = "x" * 5001
            with self.subTest(model=type(record).__name__), self.assertRaises(ValidationError):
                record.full_clean()
            self.assertEqual(len(record.notes), 5001)

    def test_archived_track_frees_position_without_rewriting_original_row(self):
        from django.utils import timezone
        original = catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, track_number=1)
        original.archived_at = timezone.now()
        original.revision += 1
        original.save()
        replacement = catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, track_number=1)
        original.refresh_from_db()
        self.assertEqual(original.track_number, 1)
        self.assertEqual(original.revision, 2)
        self.assertIsNotNone(original.archived_at)
        self.assertNotEqual(replacement.pk, original.pk)
        with self.assertRaises(ValidationError):
            catalog.ReleaseTrack.objects.create(release=self.release, recording=self.recording, track_number=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            catalog.ReleaseTrack.objects.bulk_create([catalog.ReleaseTrack(release=self.release, recording=self.recording, track_number=1)])
