from copy import deepcopy
from hashlib import sha256

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import Asset, PlatformObject, Recording, RecordingAsset, RecordingPlatformMatch
from portal.models import OperationRemoval
from provenance.collectors.lyrics import import_lyrics, lyric_text
from provenance.collectors.providers import Collected
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class LyricPayloadTests(SimpleTestCase):
    def test_only_explicit_full_unrestricted_text(self):
        base = {'has_lyrics': True, 'lyrics': {'text': 'A private fixture line\n', 'is_restricted': False}}
        self.assertEqual(lyric_text(base), base['lyrics']['text'])
        for value in (None, '', ' ', '\x00', 'é' * (100 * 1024), '\ud800', 0):
            payload = deepcopy(base); payload['lyrics']['text'] = value
            self.assertIsNone(lyric_text(payload))
        for restricted in (True, None, 0, 'false'):
            payload = deepcopy(base); payload['lyrics']['is_restricted'] = restricted
            self.assertIsNone(lyric_text(payload))
        self.assertIsNone(lyric_text({'has_lyrics': True, 'lyrics': {'snippet': 'snippet only', 'is_restricted': False}}))
        self.assertIsNone(lyric_text({'lyrics': base['lyrics']}))


class LyricImportFixture:

    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Customer', slug='lyric-customer', bootstrap_key='dezolent', management_status='managed')
        self.spotify = Service.objects.create(name='Spotify', slug='spotify')
        self.service = Service.objects.create(name='Musixmatch', slug='musixmatch')
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify,
            external_id='customer', url='https://open.spotify.com/artist/customer', verification_status='verified')
        self.conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service, status='active')
        self.job = SyncJob.objects.create(connection=self.conn, job_type='daily_public_platform', enabled=True, cursor_json={'seed_profile_link_id': str(self.profile.pk)})
        self.run = SyncRun.objects.create(sync_job=self.job)
        self.song = Recording.objects.create(organization=self.org, title='Private fixture')
        self.grant = ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song, capability='manage')
        self.seed_obj = PlatformObject.objects.create(service=self.spotify, object_type='track', external_id='spotifyfixture')
        self.seed_match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.seed_obj, review_status='accepted')
        SourceRecord.objects.create(organization=self.org, service=self.spotify,
            collector='omkar', collection_method='scraper', object_type='spotify-track', external_id='spotifyfixture',
            payload_json={'id': 'spotifyfixture', 'artists': [{'id': 'customer'}]},
            payload_hash='b' * 64, observed_at=timezone.now())
        self.obj = PlatformObject.objects.create(service=self.service, object_type='track', external_id='123')
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.obj, review_status='accepted')
        self.payload = {'track': {'id': 123}, 'has_lyrics': True,
            'lyrics': {'is_restricted': False, 'text': 'Private fixture first line\nPrivate fixture second line'},
            '_catalog_seed': {'recording_id': str(self.song.pk), 'platform': 'spotify', 'external_id': 'spotifyfixture'},
            '_catalog_identity': {'id': 123, 'external_ids': {'spotify_id': 'spotifyfixture'}}}
        self.source = SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run,
            collector='omkar', collection_method='scraper', object_type='musixmatch-track-lyrics', external_id='123',
            payload_json=self.payload, payload_hash='a' * 64, observed_at=timezone.now())
        self.row = Collected('musixmatch-track-lyrics', 'track', '123', self.payload)

    def perform(self):
        return import_lyrics(self.conn, self.obj, self.source, self.row)

class LyricImportTests(LyricImportFixture, TestCase):
    def test_private_receipt_reference_is_idempotent_without_upload_storage(self):
        with self.settings(PORTAL_UPLOADS_ENABLED=False):
            self.assertTrue(self.perform())
            self.assertFalse(self.perform())
        asset = Asset.objects.get()
        self.assertEqual((asset.source_repository, asset.source_path), ('source-record', str(self.source.pk)))
        self.assertEqual((asset.visibility, asset.rights_approved, asset.current_version_id), ('private', False, None))
        self.assertEqual(asset.sha256, sha256(self.payload['lyrics']['text'].encode()).hexdigest())
        self.assertNotIn(self.payload['lyrics']['text'], asset.description)
        self.assertTrue(ArtistResourceGrant.objects.filter(artist=self.artist, asset=asset).exists())

    def test_existing_removed_lyric_choice_prevents_import(self):
        asset = Asset.objects.create(organization=self.org, source_repository='existing', source_path='lyrics.txt', category='lyrics_text')
        relation = RecordingAsset.objects.create(recording=self.song, asset=asset, purpose='lyrics')
        OperationRemoval.objects.create(organization=self.org, model_label='catalog.recordingasset', object_id=str(relation.pk))
        self.assertFalse(self.perform())
        self.assertEqual(Asset.objects.count(), 1)

    def test_revoked_grant_or_rejected_match_blocks_stale_callers(self):
        self.grant.delete()
        self.assertFalse(self.perform())
        ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song)
        RecordingPlatformMatch.objects.filter(pk=self.match.pk).update(review_status='rejected')
        self.assertFalse(self.perform())
        self.assertFalse(Asset.objects.exists())

    def test_removed_or_revoked_seed_blocks_stale_callers(self):
        OperationRemoval.objects.create(organization=self.org, model_label='catalog.recordingplatformmatch', object_id=str(self.seed_match.pk))
        self.assertFalse(self.perform())

    def test_foreign_organization_source_is_rejected(self):
        other = Organization.objects.create(name='Other', slug='lyric-other')
        SourceRecord.objects.filter(pk=self.source.pk).update(organization=other, sync_run=None)
        self.assertFalse(self.perform())

    def test_restricted_or_snippet_only_source_is_evidence_only(self):
        for lyrics in ({'text': 'refused', 'is_restricted': True}, {'snippet': 'partial', 'is_restricted': False}):
            payload = deepcopy(self.payload); payload['lyrics'] = lyrics
            SourceRecord.objects.filter(pk=self.source.pk).update(payload_json=payload)
            self.assertFalse(self.perform())
        self.assertFalse(Asset.objects.exists())

    def test_removed_native_match_and_disabled_job_block_import(self):
        removal = OperationRemoval.objects.create(organization=self.org,
            model_label='catalog.recordingplatformmatch', object_id=str(self.match.pk))
        self.assertFalse(self.perform())
        removal.delete()
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        self.assertFalse(self.perform())
        self.assertFalse(Asset.objects.exists())

    def test_source_record_and_connection_are_reread(self):
        SourceRecord.objects.filter(pk=self.source.pk).update(collector='other')
        self.assertFalse(self.perform())
        SourceRecord.objects.filter(pk=self.source.pk).update(collector='omkar')
        Connection.objects.filter(pk=self.conn.pk).update(status='inactive')
        self.assertFalse(self.perform())

    def test_noncustomer_or_archived_recording_does_not_receive_lyrics(self):
        Artist.objects.filter(pk=self.artist.pk).update(management_status='unmanaged')
        self.assertFalse(self.perform())
        Artist.objects.filter(pk=self.artist.pk).update(management_status='managed')
        Recording.objects.filter(pk=self.song.pk).update(archived_at=timezone.now())
        self.assertFalse(self.perform())
