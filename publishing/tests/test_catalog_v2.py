"""Reviewed catalog fields remain explicitly public and version compatible."""
import copy
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from artists.models import Artist, ArtistResourceGrant, Organization
from catalog.models import Asset, AssetVersion, Recording, RecordingAsset, RecordingIdentifier
from publishing import projection
from publishing.models import ApprovedCatalogRevision, Website, WebsiteApprovedState


@override_settings(ROOT_URLCONF='publishing.public_urls')
class CatalogV2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Public studio', slug='public-studio')
        cls.artist = Artist.objects.create(organization=cls.org, name='Display artist', slug='display-artist', management_status='managed')
        cls.website = Website.objects.create(organization=cls.org, artist=cls.artist, domain='catalog.example.test')
        cls.staff = get_user_model().objects.create_user(username='catalog-reviewer', is_staff=True)
        cls.recording = Recording.objects.create(organization=cls.org, title='Song', status='approved')
        ArtistResourceGrant.objects.create(artist=cls.artist, recording=cls.recording, capability='read')

    def preview(self, **selection):
        return projection.preview_catalog(self.staff, self.website, {'recordings': [str(self.recording.pk)], **selection})

    def test_new_approvals_use_schema_two_on_existing_public_route(self):
        revision = projection.approve_catalog(self.staff, self.website, self.preview())
        self.assertEqual(revision.schema_version, 2)
        response = self.client.get('/artists/display-artist/recordings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['schema_version'], 2)

    def test_model_archived_track_is_excluded_without_portal_audit_event(self):
        from django.utils import timezone
        from catalog.models import Release, ReleaseTrack
        release = Release.objects.create(organization=self.org,title='Edition',status='approved')
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='read')
        ReleaseTrack.objects.create(release=release,recording=self.recording,track_number=1,archived_at=timezone.now())
        payload = self.preview(releases=[str(release.pk)]).payload
        self.assertEqual(payload['releases'][0]['tracks'],[])

    def test_legacy_schema_one_revision_remains_valid_and_readable(self):
        payload = {'artist': copy.deepcopy(self.preview().payload['artist']), 'recordings': [], 'releases': [], 'fanlinks': []}
        selection = {key: [] for key in ('recordings', 'releases', 'fanlinks', 'artwork')}
        revision = ApprovedCatalogRevision.objects.create(
            website=self.website, schema_version=1, content_version=1, approver=self.staff,
            payload=payload, selection=selection, checksum=projection.digest(payload), source_checksum='a' * 64,
        )
        WebsiteApprovedState.objects.create(website=self.website, active_revision=revision, approved_slug=self.artist.slug)
        revision.full_clean()
        response = self.client.get('/artists/display-artist/recordings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['schema_version'], 1)
        self.assertEqual(response.json()['results'], [])
        with self.assertRaises(ValidationError):
            projection.validate_payload(payload, schema_version=999)

    def test_identifiers_are_typed_allowlisted_and_rejected_values_are_excluded(self):
        for scheme, value, status in [
            ('ISRC', 'USABC2400001', 'verified'),
            ('MBID', '6d8f88b9-18ed-48ea-8c8a-2234d1b3ffb1', 'unverified'),
            ('ISWC', 'T-123.456.789-0', 'verified'),
            ('PRIVATE_CONTRACT', 'PRIVATE_SENTINEL', 'verified'),
            ('ISRC', 'INVALID_SENTINEL', 'verified'),
            ('ISRC', 'USABC2400002', 'rejected'),
        ]:
            RecordingIdentifier.objects.create(recording=self.recording, scheme=scheme, value=value, verification_status=status)
        row = self.preview().payload['recordings'][0]
        self.assertEqual({(item['scheme'], item['value']) for item in row['identifiers']}, {
            ('ISRC', 'USABC2400001'), ('MBID', '6d8f88b9-18ed-48ea-8c8a-2234d1b3ffb1'),
        })
        self.assertNotIn('SENTINEL', str(row))
        corrupted = self.preview().payload
        corrupted['recordings'][0]['identifiers'].append({'scheme': 'PRIVATE_CONTRACT', 'value': 'secret'})
        with self.assertRaises(ValidationError): projection.validate_payload(corrupted)

    def make_lyrics(self, content=b'Approved lyric line\nSecond line', **overrides):
        asset = Asset.objects.create(organization=self.org, storage_key='PRIVATE_LOCATION_SENTINEL',
            display_name='Lyric variant', category='lyrics_text', visibility='public', rights_basis='Owner',
            rights_approved=True, **overrides)
        version = AssetVersion.objects.create(asset=asset, file=ContentFile(content, name='PRIVATE_NAME_SENTINEL.txt'),
            original_name='PRIVATE_NAME_SENTINEL.txt', byte_size=len(content), mime_type='text/plain',
            sha256=__import__('hashlib').sha256(content).hexdigest(), creator=self.staff)
        asset.current_version = version
        asset.save()
        RecordingAsset.objects.create(recording=self.recording, asset=asset, purpose='lyrics')
        ArtistResourceGrant.objects.create(artist=self.artist, asset=asset, capability='read')
        return asset, version

    def test_lyrics_require_selection_rights_and_safe_immutable_upload(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset, version = self.make_lyrics()
            self.assertEqual(self.preview().payload['recordings'][0]['lyrics'], [])
            preview = self.preview(lyrics=[str(asset.pk)])
            lyrics = preview.payload['recordings'][0]['lyrics']
            self.assertEqual(lyrics, [{'id': projection.public_id('lyrics', version.pk), 'text': 'Approved lyric line\nSecond line', 'mime_type': 'text/plain'}])
            self.assertNotIn('SENTINEL', str(preview.payload))
            revision = projection.approve_catalog(self.staff, self.website, preview)
            self.assertEqual(self.client.get('/artists/display-artist/recordings/').json()['results'][0]['lyrics'], lyrics)
            asset.rights_approved = False
            asset.save()
            self.assertEqual(self.client.get('/artists/display-artist/recordings/').status_code, 404)
            with self.assertRaises(ValidationError): self.preview(lyrics=[str(asset.pk)])
            self.assertEqual(revision.payload['recordings'][0]['lyrics'], lyrics)

    def test_lyrics_fail_closed_for_missing_invalid_or_oversized_bytes(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            for content in (b'bad\x00binary', b'\xff', b'x' * (256 * 1024 + 1)):
                asset, _ = self.make_lyrics(content)
                with self.subTest(size=len(content)), self.assertRaises(ValidationError):
                    self.preview(lyrics=[str(asset.pk)])
            asset, version = self.make_lyrics()
            version.file.storage.delete(version.file.name)
            with self.assertRaises(ValidationError): self.preview(lyrics=[str(asset.pk)])

    def test_labels_release_genres_and_edition_metadata_exclude_party_evidence(self):
        from catalog.models import Label, RecordingLabel, Release, ReleaseLabel, ReleaseGenre, Genre, ReleaseTrack, ReleaseArtist
        from rights.models import Party
        party = Party.objects.create(organization=self.org, party_type='label', display_name='PRIVATE_PARTY_SENTINEL', legal_name='PRIVATE_LEGAL_SENTINEL')
        label = Label.objects.create(organization=self.org, name='Public imprint', party=party)
        ArtistResourceGrant.objects.create(artist=self.artist, label=label, capability='read')
        RecordingLabel.objects.create(recording=self.recording, label=label, is_primary=True)
        release = Release.objects.create(organization=self.org, title='Edition', status='approved', label_party=party, edition='Deluxe', country='USA', language='eng', script='Latn', packaging='Digital')
        ArtistResourceGrant.objects.create(artist=self.artist, release=release, capability='read')
        ReleaseLabel.objects.create(release=release, label=label, is_primary=True, catalog_number='PUB-001')
        ReleaseArtist.objects.create(release=release, artist=self.artist, credited_as='Credited artist', join_phrase=' & ')
        genre = Genre.objects.create(name='Electronic', slug='electronic')
        ReleaseGenre.objects.create(release=release, genre=genre)
        ReleaseTrack.objects.create(release=release, recording=self.recording, track_number=1, duration_ms=123000, credit_text='Track artist')
        preview = self.preview(releases=[str(release.pk)])
        self.assertEqual(preview.payload['recordings'][0]['labels'][0]['name'], 'Public imprint')
        row = preview.payload['releases'][0]
        self.assertEqual(row['labels'][0]['catalog_number'], 'PUB-001')
        self.assertEqual(row['genres'], [{'name': 'Electronic', 'slug': 'electronic'}])
        self.assertEqual(row['edition'], 'Deluxe')
        self.assertEqual(row['duration_ms'], 123000)
        self.assertFalse(row['duration_incomplete'])
        self.assertEqual(row['artists'][0]['name'], 'Credited artist')
        self.assertNotIn('SENTINEL', str(preview.payload))
        corrupted = copy.deepcopy(preview.payload)
        corrupted['releases'][0]['labels'][0]['legal_name'] = 'secret'
        with self.assertRaises(ValidationError): projection.validate_payload(corrupted)

    def test_work_identifiers_require_explicit_selection_grants_and_verified_relationship(self):
        from rights.models import MusicalWork, RecordingWork, WorkIdentifier
        work = MusicalWork.objects.create(organization=self.org, title='Composition', iswc='T-123.456.789-0')
        link = RecordingWork.objects.create(recording=self.recording, work=work, verification_status='unverified')
        WorkIdentifier.objects.create(work=work, scheme='MBID', value='6d8f88b9-18ed-48ea-8c8a-2234d1b3ffb1')
        self.assertEqual(self.preview().payload['recordings'][0]['works'], [])
        with self.assertRaises(ValidationError): self.preview(works=[str(work.pk)])
        grant = ArtistResourceGrant.objects.create(artist=self.artist, work=work, capability='read')
        self.assertEqual(self.preview(works=[str(work.pk)]).payload['recordings'][0]['works'], [])
        link.verification_status='verified'; link.save()
        preview = self.preview(works=[str(work.pk)])
        row = preview.payload['recordings'][0]['works'][0]
        self.assertEqual(row['title'], 'Composition')
        self.assertEqual({item['scheme'] for item in row['identifiers']}, {'ISWC', 'MBID'})
        projection.approve_catalog(self.staff, self.website, preview)
        self.assertEqual(self.client.get('/artists/display-artist/recordings/').status_code, 200)
        grant.delete()
        self.assertEqual(self.client.get('/artists/display-artist/recordings/').status_code, 404)

    def test_legacy_nonempty_catalog_uses_exact_version_one_fields(self):
        from catalog.models import RecordingArtist, Release, ReleaseArtist, ReleaseTrack
        RecordingArtist.objects.create(recording=self.recording, artist=self.artist)
        release = Release.objects.create(organization=self.org, title='Legacy release', status='approved')
        ArtistResourceGrant.objects.create(artist=self.artist, release=release, capability='read')
        ReleaseArtist.objects.create(release=release, artist=self.artist)
        ReleaseTrack.objects.create(release=release, recording=self.recording, track_number=1)
        preview = self.preview(releases=[str(release.pk)])
        payload = copy.deepcopy(preview.payload)
        payload['recordings'] = [{key: value for key, value in item.items() if key in projection.V1_KEYS['recording']} for item in payload['recordings']]
        payload['releases'] = [{key: value for key, value in item.items() if key in projection.V1_KEYS['release']} for item in payload['releases']]
        for item in [*payload['recordings'], *payload['releases']]:
            item['artists'] = [{key: value for key, value in credit.items() if key in projection.V1_KEYS['credit']} for credit in item['artists']]
        for item in payload['releases']:
            item['tracks'] = [{key: value for key, value in track.items() if key in projection.V1_KEYS['track']} for track in item['tracks']]
        selection = {key: value for key, value in preview.selection.items() if key not in ('lyrics', 'works')}
        revision = ApprovedCatalogRevision.objects.create(website=self.website, schema_version=1, content_version=1,
            approver=self.staff, payload=payload, selection=selection, checksum=projection.digest(payload), source_checksum='b' * 64)
        WebsiteApprovedState.objects.create(website=self.website, active_revision=revision, approved_slug=self.artist.slug)
        response = self.client.get('/artists/display-artist/recordings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()['results'][0]), projection.V1_KEYS['recording'])
        with self.assertRaises(ValidationError): projection.validate_payload(payload, schema_version=2)

    def test_release_group_and_media_are_distinct_public_catalog_entities(self):
        from catalog.models import Release, ReleaseGroup, Medium, ReleaseTrack
        group = ReleaseGroup.objects.create(organization=self.org, title='Album group', primary_type='album', secondary_types=['live'], status='approved')
        release = Release.objects.create(organization=self.org, title='Live edition', status='approved', release_group=group)
        ArtistResourceGrant.objects.create(artist=self.artist, release=release, capability='read')
        ArtistResourceGrant.objects.create(artist=self.artist, release_group=group, capability='read')
        medium = Medium.objects.create(release=release, position=1, title='Disc one', format='Digital')
        ReleaseTrack.objects.create(release=release, recording=self.recording, medium=medium, track_number=1)
        row = self.preview(releases=[str(release.pk)]).payload['releases'][0]
        self.assertEqual(row['release_group']['title'], 'Album group')
        self.assertEqual(row['release_group']['secondary_types'], ['live'])
        self.assertEqual(row['media'][0]['title'], 'Disc one')
        self.assertEqual(row['tracks'][0]['medium_id'], row['media'][0]['id'])
        self.assertIsNone(row['duration_ms'])
        self.assertTrue(row['duration_incomplete'])
        self.assertNotEqual(row['release_group']['id'], row['id'])

    def test_label_grant_revocation_withdraws_reviewed_public_label(self):
        from catalog.models import Label, RecordingLabel
        label = Label.objects.create(organization=self.org, name='Imprint')
        grant = ArtistResourceGrant.objects.create(artist=self.artist, label=label, capability='read')
        RecordingLabel.objects.create(recording=self.recording, label=label)
        projection.approve_catalog(self.staff, self.website, self.preview())
        self.assertEqual(self.client.get('/artists/display-artist/recordings/').status_code, 200)
        grant.delete()
        self.assertEqual(self.client.get('/artists/display-artist/recordings/').status_code, 404)

    @override_settings(ROOT_URLCONF='config.urls')
    def test_staff_review_offers_scoped_lyrics_and_compositions(self):
        from django.urls import reverse
        from rights.models import MusicalWork
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset, _ = self.make_lyrics()
            work = MusicalWork.objects.create(organization=self.org, title='Visible composition')
            ArtistResourceGrant.objects.create(artist=self.artist, work=work, capability='read')
            MusicalWork.objects.create(organization=self.org, title='UNASSIGNED_WORK_SENTINEL')
            self.client.force_login(self.staff)
            url = reverse('portal:review', args=[self.artist.pk, self.website.pk])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="lyrics"')
            self.assertContains(response, 'Visible composition')
            self.assertNotContains(response, 'UNASSIGNED_WORK_SENTINEL')
            response = self.client.post(url, {'action': 'preview', 'recordings': [str(self.recording.pk)], 'lyrics': [str(asset.pk)], 'works': [str(work.pk)]})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Approved lyric line')
            self.assertNotContains(response, 'PRIVATE_LOCATION_SENTINEL')
