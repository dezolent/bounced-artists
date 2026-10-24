from portal.asset_display import text_preview
from django.test import TestCase
from provenance.tests.test_lyric_import import LyricImportFixture
from catalog.models import Asset
from provenance.models import SourceRecord


class LyricCandidatePreviewTests(LyricImportFixture, TestCase):
    def test_preview_reads_only_linked_source(self):
        self.assertTrue(self.perform())
        asset = Asset.objects.get()
        self.assertEqual(text_preview(asset)['text'], self.payload['lyrics']['text'])
        self.assertFalse(text_preview(asset)['unavailable'])
        self.grant.delete()
        self.assertTrue(text_preview(asset)['unavailable'])

    def test_modified_payload_or_unlinked_asset_cannot_disclose_source(self):
        self.assertTrue(self.perform())
        asset = Asset.objects.get()
        payload = dict(self.payload); payload['lyrics'] = {'text': 'Changed receipt', 'is_restricted': False}
        SourceRecord.objects.filter(pk=self.source.pk).update(payload_json=payload)
        self.assertTrue(text_preview(asset)['unavailable'])
        asset.sha256 = 'b' * 64
        asset.visibility = 'members'
        self.assertTrue(text_preview(asset)['unavailable'])

    def test_user_constructed_asset_cannot_retarget_an_existing_receipt(self):
        self.assertTrue(self.perform())
        original = Asset.objects.get()
        fake = Asset.objects.create(organization=self.org, category='lyrics_text',
            source_repository='source-record', source_path=str(self.source.pk),
            sha256=original.sha256, mime_type='text/plain')
        from catalog.models import RecordingAsset
        from artists.models import ArtistResourceGrant
        RecordingAsset.objects.create(recording=self.song, asset=fake, purpose='lyrics')
        ArtistResourceGrant.objects.create(artist=self.artist, asset=fake)
        self.assertTrue(text_preview(fake)['unavailable'])
        self.assertFalse(text_preview(original)['unavailable'])

    def test_pausing_job_preserves_preview_but_blocks_new_import(self):
        self.assertTrue(self.perform())
        asset = Asset.objects.get()
        from provenance.models import SyncJob
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        self.assertFalse(text_preview(asset)['unavailable'])
        self.assertEqual(text_preview(asset)['text'], self.payload['lyrics']['text'])
        self.assertFalse(self.perform())

    def test_paused_job_still_requires_verified_profile_grants_and_match(self):
        self.assertTrue(self.perform())
        asset = Asset.objects.get()
        from provenance.models import SyncJob
        from artists.models import ArtistProfileLink
        from catalog.models import RecordingPlatformMatch
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        ArtistProfileLink.objects.filter(pk=self.profile.pk).update(verification_status='unverified')
        self.assertTrue(text_preview(asset)['unavailable'])
        ArtistProfileLink.objects.filter(pk=self.profile.pk).update(verification_status='verified')
        RecordingPlatformMatch.objects.filter(pk=self.match.pk).update(review_status='rejected')
        self.assertTrue(text_preview(asset)['unavailable'])
        RecordingPlatformMatch.objects.filter(pk=self.match.pk).update(review_status='accepted')
        self.grant.delete()
        self.assertTrue(text_preview(asset)['unavailable'])
