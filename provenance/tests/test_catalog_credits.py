from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections, transaction, OperationalError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization
from catalog.models import ArtistPlatformMatch, Recording, RecordingArtist, RecordingPlatformMatch
from portal.models import EditorialChange, OperationRemoval
from provenance.models import AuditEvent, SourceRecord
from . import test_collector_catalog as fixtures


class CatalogCreditsTests(TestCase):
    setUp = fixtures.CollectorCatalogTests.setUp
    track = fixtures.CollectorCatalogTests.track
    ensure = fixtures.CollectorCatalogTests.ensure

    def featured_track(self, **body):
        return self.track(artists=[{'id':'cuddie','name':'CUDDIE'}, {'id':'customer','name':'Customer'}],
            credits={'contributors':[
                {'artist_id':'cuddie','name':'CUDDIE','role':'Main Artist','role_group':'Artist'},
                {'artist_id':'customer','name':'Customer','role':'Featured Artist','role_group':'Artist'},
                {'artist_id':'writer','name':'Private writer','role':'Composer','role_group':'Composition & Lyrics'},
            ]}, **body)

    def credits(self, recording):
        return list(recording.credits.order_by('billing_order','pk').values_list('artist__name','role','billing_order'))

    def legacy(self, values):
        from provenance.collectors.catalog import _evidence
        song=Recording.objects.create(organization=self.org,title=values[1].payload_json['name'])
        RecordingArtist.objects.create(recording=song,artist=self.artist,role='primary')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=song,capability='manage')
        RecordingPlatformMatch.objects.create(recording=song,platform_object=values[0],review_status='accepted',match_method='verified_platform_id')
        _evidence(song,values[1],song.title)
        return song

    def test_explicit_roles_import_all_performers_without_collaborator_access(self):
        values=self.featured_track()
        payload=values[1].payload_json.copy()
        self.assertTrue(self.ensure(values))
        song=Recording.objects.get()
        self.assertEqual(self.credits(song),[('CUDDIE','primary',1),('Customer','featured',2)])
        cuddie=song.credits.get(role='primary').artist
        self.assertEqual(cuddie.management_status,'unmanaged')
        self.assertIsNone(cuddie.bootstrap_key)
        self.assertFalse(ArtistMembership.objects.filter(artist=cuddie).exists())
        self.assertFalse(ArtistResourceGrant.objects.filter(artist=cuddie).exists())
        self.assertTrue(ArtistPlatformMatch.objects.filter(artist=cuddie,review_status='accepted').exists())
        self.assertFalse(Artist.objects.filter(name='Private writer').exists())
        before=list(song.credits.values_list('id',flat=True))
        self.assertFalse(self.ensure(values))
        self.assertEqual(list(song.credits.values_list('id',flat=True)),before)
        values[1].refresh_from_db()
        self.assertEqual(values[1].payload_json,payload)

    def test_coprimary_is_not_inferred_from_artist_order(self):
        values=self.featured_track()
        values[1].payload_json['credits']['contributors'][1]['role']='Main Artist'
        values[1].save()
        self.ensure(values)
        song=Recording.objects.get()
        self.assertEqual(self.credits(song),[('CUDDIE','primary',1),('Customer','primary',2)])

    def test_headline_artist_list_without_roles_does_not_invent_primary_or_featured(self):
        values=self.track(artists=[{'id':'cuddie','name':'CUDDIE'},{'id':'customer','name':'Customer'}])
        self.ensure(values)
        self.assertEqual(self.credits(Recording.objects.get()),[('CUDDIE','performer',1),('Customer','performer',2)])

    def test_same_name_different_platform_ids_stay_separate(self):
        values=self.featured_track()
        Artist.objects.create(organization=self.org,name='CUDDIE',slug='other-cuddie')
        self.ensure(values)
        self.assertEqual(Artist.objects.filter(name='CUDDIE').count(),2)
        self.assertNotEqual(Recording.objects.get().credits.get(role='primary').artist.slug,'other-cuddie')

    def test_replay_repairs_only_proven_legacy_auto_credit_and_preserves_ids(self):
        values=self.featured_track()
        song=self.legacy(values)
        credit_id=song.credits.get().pk
        from provenance.collectors.catalog_credits import sync_recording_credits
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'changed')
        self.assertEqual(self.credits(song),[('CUDDIE','primary',1),('Customer','featured',2)])
        self.assertEqual(song.credits.get(artist=self.artist).pk,credit_id)
        self.assertEqual(Recording.objects.count(),1)
        self.assertEqual(ArtistResourceGrant.objects.filter(recording=song).count(),1)
        self.assertTrue(AuditEvent.objects.filter(action='catalog.credits_sync',target__recording=song).exists())
        repeated=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(repeated['status'],'unchanged')

    def test_manual_credits_and_removals_are_never_overwritten(self):
        from provenance.collectors.catalog_credits import sync_recording_credits
        values=self.featured_track()
        song=self.legacy(values)
        credit=song.credits.get()
        EditorialChange.objects.create(organization=self.org,artist=self.artist,user=None,
            model_label='catalog.recordingartist',object_id=str(credit.pk),action='update',changed_fields=['role'])
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'protected')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])
        EditorialChange.objects.all().delete()
        OperationRemoval.objects.create(model_label='catalog.recordingartist',object_id=str(credit.pk))
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'protected')
        self.assertEqual(song.credits.count(),1)

    def test_ungranted_or_rejected_matches_cannot_change_credits(self):
        from provenance.collectors.catalog_credits import sync_recording_credits
        values=self.featured_track()
        song=self.legacy(values)
        match=RecordingPlatformMatch.objects.get(recording=song)
        match.review_status='rejected';match.save()
        self.assertEqual(sync_recording_credits(song,self.conn,values[1],repair_legacy=True)['status'],'unauthorized')
        match.review_status='accepted';match.save()
        ArtistResourceGrant.objects.filter(recording=song).delete()
        self.assertEqual(sync_recording_credits(song,self.conn,values[1],repair_legacy=True)['status'],'unauthorized')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])

    def test_ambiguous_credit_roles_do_not_change_legacy_primary(self):
        from provenance.collectors.catalog_credits import sync_recording_credits
        values=self.featured_track()
        song=self.legacy(values)
        values[1].payload_json['credits']['contributors'].append({'artist_id':'customer','name':'Customer','role':'Main Artist'})
        values[1].save()
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'unconfirmed')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])

    def test_credit_backfill_preview_apply_hash_and_repeat(self):
        from django.core.management.base import CommandError
        values=self.featured_track()
        song=self.legacy(values)
        self.job.enabled=True;self.job.save()
        with TemporaryDirectory() as directory:
            report=Path(directory)/'credits.json'
            out=StringIO()
            call_command('backfill_recording_credits',artist=str(self.artist.pk),report=str(report),stdout=out)
            preview=json.loads(report.read_text())
            self.assertEqual(preview['summary']['recordings_changed'],1)
            self.assertEqual(self.credits(song),[('Customer','primary',1)])
            self.assertFalse(Artist.objects.filter(name='CUDDIE').exists())
            with self.assertRaises(CommandError):
                call_command('backfill_recording_credits',artist=str(self.artist.pk),report=str(report),apply=True,expected_sha256='0'*64,stdout=StringIO())
            self.assertEqual(self.credits(song),[('Customer','primary',1)])
            call_command('backfill_recording_credits',artist=str(self.artist.pk),report=str(report),apply=True,expected_sha256=preview['sha256'],stdout=StringIO())
            self.assertEqual(self.credits(song),[('CUDDIE','primary',1),('Customer','featured',2)])
            call_command('backfill_recording_credits',artist=str(self.artist.pk),report=str(report),stdout=StringIO())
            self.assertEqual(json.loads(report.read_text())['summary']['recordings_changed'],0)
            self.assertEqual(SourceRecord.objects.count(),1)

    def test_corrected_credits_drive_appears_on_and_primary_selection(self):
        from portal.recording_roles import primary_recording_ids
        user=get_user_model().objects.create_user(username='credit-viewer')
        ArtistMembership.objects.create(user=user,artist=self.artist,role='viewer')
        self.ensure(self.featured_track())
        song=Recording.objects.get()
        self.client.force_login(user)
        url=reverse('portal:catalog',args=[self.artist.pk])
        self.assertEqual(list(self.client.get(url).context['recordings']),[])
        response=self.client.get(url,{'tab':'appears-on'})
        self.assertEqual([row.pk for row in response.context['recordings']],[song.pk])
        self.assertContains(response,'CUDDIE')
        self.assertFalse(primary_recording_ids(user,self.artist).exists())
        response=self.client.get(reverse('portal:recording',args=[self.artist.pk,song.pk]))
        self.assertContains(response,'CUDDIE')
        self.assertContains(response,'Featured')

    def test_preview_performs_no_database_writes_even_inside_rollback(self):
        values=self.featured_track()
        self.legacy(values)
        def read_only(execute,sql,params,many,context):
            self.assertNotIn(sql.lstrip().split()[0].upper(),{'INSERT','UPDATE','DELETE'})
            return execute(sql,params,many,context)
        with TemporaryDirectory() as directory,connection.execute_wrapper(read_only):
            call_command('backfill_recording_credits',report=str(Path(directory)/'plan.json'),stdout=StringIO())

    def test_malformed_initial_credits_do_not_prevent_later_confirmed_enrichment(self):
        values=self.track(artists=[{'id':'cuddie'},{'id':'customer'}])
        self.ensure(values)
        song=Recording.objects.get()
        self.assertEqual(self.credits(song),[('Customer','performer',1)])
        self.ensure(self.featured_track())
        self.assertEqual(self.credits(song),[('CUDDIE','primary',1),('Customer','featured',2)])

    def test_removed_source_receipt_cannot_repair_credits(self):
        from provenance.collectors.catalog_credits import sync_recording_credits
        values=self.featured_track()
        song=self.legacy(values)
        OperationRemoval.objects.create(model_label='provenance.sourcerecord',object_id=str(values[1].pk))
        self.assertEqual(sync_recording_credits(song,self.conn,values[1],repair_legacy=True)['status'],'unauthorized')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])

    def test_three_artist_feature_preserves_both_featured_credits(self):
        values=self.track(name='thinkin bout ya',artists=[
            {'id':'jaketracks','name':'JakeTracks'}, {'id':'fendi','name':'fendi mars'}, {'id':'customer','name':'Customer'}],
            credits={'contributors':[
                {'artist_id':'jaketracks','role':'Main Artist'},
                {'artist_id':'fendi','role':'Featured Artist'},
                {'artist_id':'customer','role':'Featured Artist'}]})
        self.ensure(values)
        self.assertEqual(self.credits(Recording.objects.get()),
            [('JakeTracks','primary',1),('fendi mars','featured',2),('Customer','featured',3)])

    def test_headline_only_alias_cannot_erase_confirmed_roles(self):
        self.ensure(self.featured_track())
        song=Recording.objects.get()
        before=self.credits(song)
        self.ensure(self.track(endpoint='spotify-track',artists=[
            {'id':'cuddie','name':'CUDDIE'}, {'id':'customer','name':'Customer'}]))
        self.assertEqual(self.credits(song),before)

    def test_rejected_artist_identity_cannot_be_recreated(self):
        from provenance.collectors.catalog_credits import sync_recording_credits
        from catalog.models import PlatformObject
        values=self.featured_track()
        song=self.legacy(values)
        cuddie=Artist.objects.create(organization=self.org,name='CUDDIE',slug='rejected-cuddie')
        obj=PlatformObject.objects.create(service=self.service,object_type='artist',external_id='cuddie')
        ArtistPlatformMatch.objects.create(artist=cuddie,platform_object=obj,review_status='rejected')
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'protected')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])
        self.assertEqual(Artist.objects.filter(name='CUDDIE').count(),1)

    def test_persisted_import_credit_mapping_is_protected(self):
        from publishing.models import Website
        from provenance.collectors.catalog_credits import sync_recording_credits
        values=self.featured_track()
        song=self.legacy(values)
        Website.objects.create(organization=self.org,artist=self.artist,domain='credit-import.example.test',
            theme_config={'dezolent_import':{'entity_ids':{
                'recording_credit:known:customer:primary':str(song.credits.get().pk)}}})
        result=sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
        self.assertEqual(result['status'],'protected')
        self.assertEqual(self.credits(song),[('Customer','primary',1)])


class ConcurrentCreditRepairTests(TransactionTestCase):
    track = fixtures.CollectorCatalogTests.track
    featured_track = CatalogCreditsTests.featured_track
    legacy = CatalogCreditsTests.legacy

    def setUp(self):
        Organization.objects.get_or_create(slug='bounced-studio',defaults={'name':'Bounced Studio'})
        fixtures.CollectorCatalogTests.setUp(self)

    def test_credit_cannot_be_edited_between_snapshot_and_repair(self):
        from provenance.collectors import catalog_credits
        values=self.featured_track()
        song=self.legacy(values)
        credit=song.credits.get()
        entered,release=Event(),Event()
        original=catalog_credits._snapshot
        paused=False
        def pause_after_snapshot(recording):
            nonlocal paused
            result=original(recording)
            if not paused:
                paused=True
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Concurrent credit test did not release repair')
            return result
        def repair():
            try:
                return catalog_credits.sync_recording_credits(song,self.conn,values[1],repair_legacy=True)
            finally:
                connections.close_all()
        blocked=False
        with patch.object(catalog_credits,'_snapshot',new=pause_after_snapshot),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(repair)
            try:
                self.assertTrue(entered.wait(10))
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '100ms'")
                        RecordingArtist.objects.filter(pk=credit.pk).update(role='guest')
                except OperationalError:
                    blocked=True
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10)['status'],'changed')
        self.assertTrue(blocked,'A concurrent artist-credit edit could be overwritten by repair')
