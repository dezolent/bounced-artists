from django.test import TestCase
from django.test import SimpleTestCase
from django.utils import timezone

from artists.models import Artist, ArtistResourceGrant, Organization
from catalog.models import Asset, Recording, RecordingArtist, RecordingAsset, RecordingIdentifier


class RecordingTitleIdentityTests(SimpleTestCase):
    def test_feature_credits_do_not_hide_remix_or_edit_suffix(self):
        from catalog.reconciliation import title_key
        self.assertNotEqual(title_key('Tell Me feat. James Queen - Remix'), title_key('Tell Me'))
        self.assertNotEqual(title_key('Tell Me (feat. James Queen) [Radio Edit]'), title_key('Tell Me'))
        self.assertEqual(title_key('Tell Me (feat. James Queen)'), title_key('Tell Me'))


class ReconciliationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Customer', slug='reconcile-customer')

    def recording(self, title='Tell Me', **values):
        row = Recording.objects.create(organization=self.org, title=title, duration_ms=180000, **values)
        RecordingArtist.objects.create(recording=row, artist=self.artist)
        ArtistResourceGrant.objects.create(artist=self.artist, recording=row, capability='manage')
        RecordingIdentifier.objects.create(recording=row, scheme='isrc', value='US1234567890')
        return row

    def test_matching_requires_corroboration_and_keeps_distinct_titles_separate(self):
        from catalog.reconciliation import resolve_candidate
        row = self.recording()
        payload = {'title':'Tell Me (feat. James Queen)', 'duration_ms':180300, 'isrc':'US1234567890'}
        match, candidates = resolve_candidate(self.artist, payload)
        self.assertEqual(match, row)
        self.assertEqual(candidates, [row])
        self.assertIsNone(resolve_candidate(self.artist, {'title':'Tell Me', 'duration_ms':180000})[0])
        self.assertEqual(resolve_candidate(self.artist, {'title':'Chasing Ghosts','isrc':'US1234567890','duration_ms':180000}), (None, []))
        self.assertEqual(resolve_candidate(self.artist, {'title':'Tell Me (Live)','isrc':'US1234567890','duration_ms':180000}), (None, []))

    def test_duplicate_plan_dry_apply_retains_uuid_evidence_and_is_idempotent(self):
        from catalog.reconciliation import build_plan, apply_plan
        from provenance.models import EntityTarget, FieldObservation, SourceRecord
        canonical = self.recording()
        duplicate = self.recording()
        target = EntityTarget.objects.create(organization=self.org, recording=duplicate)
        source = SourceRecord.objects.create(organization=self.org, collector='test', collection_method='manual', object_type='track', external_id='x', payload_json={'title':'Tell Me'}, payload_hash='a'*64, observed_at=timezone.now())
        observation = FieldObservation.objects.create(source_record=source,target=target,field_name='title',value_json='Tell Me')
        plan = build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        self.assertFalse(plan['conflicts'])
        duplicate.refresh_from_db()
        self.assertIsNone(duplicate.archived_at)
        result = apply_plan(plan, expected_hash=plan['plan_hash'])
        duplicate.refresh_from_db(); target.refresh_from_db(); source.refresh_from_db()
        self.assertIsNotNone(duplicate.archived_at)
        self.assertEqual(target.recording_id, canonical.pk)
        self.assertTrue(FieldObservation.objects.filter(pk=observation.pk,source_record=source).exists())
        self.assertEqual(source.payload_json, {'title':'Tell Me'})
        self.assertEqual(result['recordings_archived'],1)
        self.assertEqual(apply_plan(plan, expected_hash=plan['plan_hash'])['already_applied'],True)

    def test_stale_plan_and_conflicting_identity_fail_without_changes(self):
        from catalog.reconciliation import build_plan, apply_plan
        canonical, duplicate = self.recording(), self.recording()
        plan = build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        duplicate.notes='new editorial note'; duplicate.save()
        with self.assertRaisesMessage(ValueError, 'changed'):
            apply_plan(plan, expected_hash=plan['plan_hash'])
        self.assertFalse(Recording.objects.filter(archived_at__isnull=False).exists())
        other=self.recording('Chasing Ghosts')
        self.assertTrue(build_plan(recording_pairs=[(str(other.pk),str(canonical.pk))])['conflicts'])

    def test_unedited_missing_language_is_preserved_on_the_canonical_recording(self):
        from catalog.reconciliation import build_plan, apply_plan
        canonical, duplicate = self.recording(), self.recording(language='en-US')
        plan = build_plan(recording_pairs=[(str(duplicate.pk), str(canonical.pk))])
        self.assertFalse(plan['conflicts'])
        apply_plan(plan, expected_hash=plan['plan_hash'])
        canonical.refresh_from_db(); duplicate.refresh_from_db()
        self.assertEqual(canonical.language, 'en-US')
        self.assertEqual(duplicate.language, 'en-US')
        self.assertIsNotNone(duplicate.archived_at)

    def test_distinct_or_deliberately_cleared_language_remains_a_conflict(self):
        from catalog.reconciliation import build_plan
        from portal.models import EditorialChange
        canonical, duplicate = self.recording(), self.recording(language='en-US')
        EditorialChange.objects.create(organization=self.org, artist=self.artist, model_label='catalog.recording', object_id=str(canonical.pk),
            action='update', changed_fields=['language'])
        self.assertTrue(build_plan(recording_pairs=[(str(duplicate.pk), str(canonical.pk))])['conflicts'])
        canonical.language='fr'; canonical.save()
        self.assertTrue(build_plan(recording_pairs=[(str(duplicate.pk), str(canonical.pk))])['conflicts'])

    def test_identical_cover_assets_are_archived_and_edited_cover_is_preserved(self):
        from catalog.reconciliation import build_plan, apply_plan
        canonical=self.recording()
        cover=Asset.objects.create(organization=self.org,category='cover_art',source_url='https://i.scdn.co/image/abc',mime_type='image/jpeg')
        duplicate=Asset.objects.create(organization=self.org,category='cover_art',source_url=cover.source_url,mime_type='image/jpeg')
        edited=Asset.objects.create(organization=self.org,category='cover_art',source_url=cover.source_url,mime_type='image/jpeg',alt_text='Editorial alt')
        RecordingAsset.objects.create(recording=canonical,asset=cover,purpose='cover',is_primary=True)
        RecordingAsset.objects.create(recording=canonical,asset=duplicate,purpose='cover')
        RecordingAsset.objects.create(recording=canonical,asset=edited,purpose='cover')
        plan=build_plan()
        self.assertEqual(len(plan['asset_merges']),1)
        apply_plan(plan,expected_hash=plan['plan_hash'])
        duplicate.refresh_from_db(); edited.refresh_from_db()
        self.assertIsNotNone(duplicate.archived_at)
        self.assertIsNone(edited.archived_at)

    def test_rollback_check_leaves_original_rows_and_no_receipt(self):
        from catalog.reconciliation import build_plan, apply_plan
        from provenance.models import AuditEvent
        canonical, duplicate=self.recording(),self.recording()
        plan=build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        result=apply_plan(plan,expected_hash=plan['plan_hash'],rollback=True)
        self.assertTrue(result['rolled_back'])
        duplicate.refresh_from_db()
        self.assertIsNone(duplicate.archived_at)
        self.assertFalse(AuditEvent.objects.filter(action='catalog.reconcile').exists())

    def test_multi_duplicate_and_cover_remaps_respect_combined_unique_constraints(self):
        from catalog.reconciliation import build_plan,apply_plan
        canonical,duplicate,third=self.recording(),self.recording(),self.recording()
        for recording in (canonical,duplicate,third):
            cover=Asset.objects.create(organization=self.org,category='cover_art',source_url='https://i.scdn.co/image/shared',mime_type='image/jpeg')
            RecordingAsset.objects.create(recording=recording,asset=cover,purpose='cover',is_primary=True)
        plan=build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk)),(str(third.pk),str(canonical.pk))])
        self.assertFalse(plan['conflicts'])
        result=apply_plan(plan,expected_hash=plan['plan_hash'])
        self.assertEqual(result['recordings_archived'],2)
        self.assertEqual(result['assets_archived'],2)
        self.assertEqual(RecordingAsset.objects.filter(recording=canonical,asset__archived_at__isnull=True).count(),1)

    def test_rejected_match_decision_collision_blocks_whole_plan(self):
        from catalog.reconciliation import build_plan
        from artists.models import Service
        from catalog.models import PlatformObject,RecordingPlatformMatch
        canonical,duplicate=self.recording(),self.recording()
        service=Service.objects.create(name='Test platform',slug='reconcile-test')
        obj=PlatformObject.objects.create(service=service,object_type='track',external_id='x')
        RecordingPlatformMatch.objects.create(recording=canonical,platform_object=obj,review_status='rejected')
        RecordingPlatformMatch.objects.create(recording=duplicate,platform_object=obj,review_status='accepted')
        plan=build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        self.assertTrue(plan['conflicts'])

    def test_rejected_platform_alias_blocks_accepted_native_identity(self):
        from catalog.reconciliation import build_plan
        from artists.models import Service
        from catalog.models import PlatformObject, RecordingPlatformMatch
        canonical, duplicate = self.recording(), self.recording()
        service = Service.objects.create(name='Spotify', slug='spotify')
        legacy = PlatformObject.objects.create(service=service, object_type='recording', url='https://open.spotify.com/track/1234567890ABCDEFGHIJKL')
        native = PlatformObject.objects.create(service=service, object_type='track', external_id='1234567890ABCDEFGHIJKL')
        RecordingPlatformMatch.objects.create(recording=canonical, platform_object=legacy, review_status='rejected')
        RecordingPlatformMatch.objects.create(recording=duplicate, platform_object=native, review_status='accepted')
        plan = build_plan(recording_pairs=[(str(duplicate.pk), str(canonical.pk))])
        self.assertTrue(plan['conflicts'])

    def test_planning_rejects_a_catalog_change_during_plan_construction(self):
        from unittest.mock import patch
        from catalog.reconciliation import build_plan
        with patch('catalog.reconciliation.state_fingerprint', side_effect=[{'before': 1}, {'after': 2}]):
            with self.assertRaisesMessage(ValueError, 'changed during planning'):
                build_plan()

    def test_accepted_soundcloud_permalink_overlap_corroborates_missing_isrc(self):
        from catalog.reconciliation import build_plan,apply_plan,recording_identity_ids
        from artists.models import Service
        from catalog.models import PlatformObject,RecordingPlatformMatch
        canonical,duplicate=self.recording(),self.recording()
        RecordingIdentifier.objects.filter(recording=duplicate).delete()
        service=Service.objects.create(name='SoundCloud',slug='soundcloud')
        for recording,external_id,url in [(canonical,'','https://soundcloud.com/customer/tell-me/s-PrivateShare?utm_source=site'),
                (duplicate,'123','https://soundcloud.com/customer/tell-me')]:
            obj=PlatformObject.objects.create(service=service,object_type='recording' if not external_id else 'track',external_id=external_id,url=url)
            RecordingPlatformMatch.objects.create(recording=recording,platform_object=obj,review_status='accepted')
        plan=build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        self.assertFalse(plan['conflicts'])
        apply_plan(plan,expected_hash=plan['plan_hash'])
        self.assertEqual(RecordingPlatformMatch.objects.filter(recording=canonical,review_status='accepted').count(),2)
        self.assertEqual(recording_identity_ids(canonical),{str(canonical.pk),str(duplicate.pk)})

    def test_exception_during_receipt_rolls_back_all_relationship_moves(self):
        from unittest.mock import patch
        from catalog.reconciliation import build_plan,apply_plan
        from provenance.models import EntityTarget
        canonical,duplicate=self.recording(),self.recording()
        target=EntityTarget.objects.create(organization=self.org,recording=duplicate)
        plan=build_plan(recording_pairs=[(str(duplicate.pk),str(canonical.pk))])
        with patch('catalog.reconciliation.AuditEvent.objects.create',side_effect=RuntimeError('receipt failed')):
            with self.assertRaises(RuntimeError):
                apply_plan(plan,expected_hash=plan['plan_hash'])
        duplicate.refresh_from_db(); target.refresh_from_db()
        self.assertIsNone(duplicate.archived_at)
        self.assertEqual(target.recording_id,duplicate.pk)
