import os
from io import StringIO
import json
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import Recording, RecordingPlatformMatch, PlatformObject
from provenance.models import Connection, SyncJob, SyncRun, SourceRecord
from provenance.collectors.providers import Collected
from provenance.collectors.http import CollectionError


class SelfHostedIntegrationTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Dezolent',slug='selfhost-dezolent',bootstrap_key='dezolent',management_status='managed')
        self.spotify,_=Service.objects.get_or_create(slug='spotify',defaults={'name':'Spotify'})
        self.profile=ArtistProfileLink.objects.create(artist=self.artist,platform=self.spotify,url='https://open.spotify.com/artist/artist123',verification_status='verified')
        self.conn=Connection.objects.create(organization=self.org,artist=self.artist,service=self.spotify,status='active')
        self.job=SyncJob.objects.create(connection=self.conn,enabled=True,job_type='daily_public_platform',cursor_json={'profile_link_id':str(self.profile.pk)})
        self.song=Recording.objects.create(organization=self.org,title='Known recording')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song)
        obj=PlatformObject.objects.create(service=self.spotify,object_type='track',external_id='track123',url='https://open.spotify.com/track/track123')
        RecordingPlatformMatch.objects.create(recording=self.song,platform_object=obj,review_status='accepted')
        SourceRecord.objects.create(organization=self.org,service=self.spotify,collector='omkar',collection_method='scraper',
            object_type='track-details',external_id='track123',observed_at=timezone.now(),payload_hash='a'*64,
            payload_json={'id':'track123','artists':[{'id':'artist123'}]})

    def test_enrichment_configuration_is_opt_in_and_follows_verified_customer_catalog(self):
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'hosted','ARTIST_SYNC_CATALOG_ENRICHMENT':'0'}):
            call_command('configure_artist_sync',apply=True,stdout=StringIO())
        self.assertEqual(SyncJob.objects.count(),1)
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SYNC_CATALOG_ENRICHMENT':'1'}):
            call_command('configure_artist_sync',apply=True,stdout=StringIO())
        job=SyncJob.objects.get(cursor_json__enrichment='spotify-audio-features')
        self.assertEqual(job.cursor_json['seed_profile_link_id'],str(self.profile.pk))
        self.assertEqual(job.connection.artist_id,self.artist.pk)
        self.profile.verification_status='unverified';self.profile.save()
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SYNC_CATALOG_ENRICHMENT':'1'}):
            call_command('configure_artist_sync',apply=True,stdout=StringIO())
        job.refresh_from_db();self.assertFalse(job.enabled)

    def test_musixmatch_jobs_are_disabled_and_never_dispatched(self):
        service = Service.objects.create(name='Musixmatch', slug='musixmatch')
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=service, status='active')
        target = SyncJob.objects.create(connection=conn, job_type='daily_public_platform', enabled=True,
            cursor_json={'seed_profile_link_id': str(self.profile.pk)})
        with patch.dict(os.environ,{'ARTIST_SYNC_CATALOG_ENRICHMENT':'1'}):
            call_command('configure_artist_sync',apply=True,stdout=StringIO())
        target.refresh_from_db()
        self.assertFalse(target.enabled)
        target.enabled = True
        target.save(update_fields=['enabled'])
        SyncJob.objects.exclude(pk=target.pk).update(enabled=False)
        from provenance.collectors.runner import run_due
        with patch('provenance.collectors.runner.collect',side_effect=AssertionError('retired scraper dispatched')):
            result=run_due()
        self.assertEqual(result['requests'],0)
        self.assertFalse(SyncRun.objects.exists())

    def test_backend_change_does_not_reuse_identical_cloud_snapshot(self):
        from provenance.collectors.runner import persist,profile_fingerprint
        now=timezone.now()
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'hosted'}):
            hosted=profile_fingerprint('spotify',self.profile.url)
        first=SyncRun.objects.create(sync_job=self.job,counts_json={'profile_fingerprint':hosted})
        row=Collected('artist-details','artist','artist123',{'id':'artist123'})
        self.assertTrue(persist(first,row,now))
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SCRAPER_SPOTIFY_URL':'http://127.0.0.1:18100'}):
            own=profile_fingerprint('spotify',self.profile.url)
        second=SyncRun.objects.create(sync_job=self.job,counts_json={'profile_fingerprint':own})
        self.assertTrue(persist(second,row,now))
        self.assertEqual(SourceRecord.objects.filter(object_type='artist-details').count(),2)

    def test_catalog_enrichment_resumes_after_previous_seed_checkpoint(self):
        from provenance.collectors.runner import profile_fingerprint,run_due
        other=Recording.objects.create(organization=self.org,title='Another known recording')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=other)
        obj=PlatformObject.objects.create(service=self.spotify,object_type='track',external_id='track456')
        RecordingPlatformMatch.objects.create(recording=other,platform_object=obj,review_status='accepted')
        SourceRecord.objects.create(organization=self.org,service=self.spotify,collector='omkar',collection_method='scraper',
            object_type='track-details',external_id='track456',observed_at=timezone.now(),payload_hash='b'*64,
            payload_json={'id':'track456','artists':[{'id':'artist123'}]})
        with patch.dict(os.environ,{'ARTIST_SYNC_CATALOG_ENRICHMENT':'1'}):
            call_command('configure_artist_sync',apply=True,stdout=StringIO())
        target=SyncJob.objects.get(cursor_json__enrichment='spotify-audio-features')
        SyncJob.objects.exclude(pk=target.pk).update(enabled=False)
        fingerprint=profile_fingerprint('spotify-audio-features',self.profile.url)
        prior=SyncRun.objects.create(sync_job=target,status='failed',started_at=timezone.now(),
            counts_json={'profile_fingerprint':fingerprint,'catalog_seed_cursor':str(self.song.pk)+':track123'})
        interrupted=SyncRun.objects.create(sync_job=target,status='running',started_at=timezone.now(),counts_json={'profile_fingerprint':fingerprint})
        def fake_collect(platform,url,budget,**options):
            self.assertEqual([s['external_id'] for s in options['catalog_seeds']],['track456','track123'])
            options['seed_finished'](options['catalog_seeds'][0])
            raise CollectionError('job_time_limit')
            yield  # Ensure the error occurs while iterating.
        with patch('provenance.collectors.runner.collect',side_effect=fake_collect):
            result=run_due()
        self.assertEqual(result['failed'],1)
        run=SyncRun.objects.exclude(pk__in=[prior.pk,interrupted.pk]).get()
        self.assertEqual(run.counts_json['catalog_seed_cursor'],str(other.pk)+':track456')
        self.assertEqual(run.counts_json['catalog_seeds_processed'],1)
        self.assertEqual(run.counts_json['catalog_seed_count'],2)

    def test_configuration_retires_shazam_preserving_evidence_and_creates_official_features_job(self):
        service, _ = Service.objects.get_or_create(slug='shazam', defaults={'name': 'Shazam'})
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=service, status='active')
        old = SyncJob.objects.create(connection=conn, job_type='daily_public_platform', enabled=True,
            cursor_json={'seed_profile_link_id': str(self.profile.pk)})
        run = SyncRun.objects.create(sync_job=old)
        source = SourceRecord.objects.create(organization=self.org, service=service, sync_run=run,
            collector='omkar', collection_method='scraper', object_type='shazam-audio-analysis',
            external_id='42', payload_json={'bpm': 120}, payload_hash='d' * 64, observed_at=timezone.now())
        with patch.dict(os.environ, {'ARTIST_SYNC_CATALOG_ENRICHMENT': '1'}):
            call_command('configure_artist_sync', apply=True, stdout=StringIO())
            call_command('configure_artist_sync', apply=True, stdout=StringIO())
        old.refresh_from_db()
        self.assertFalse(old.enabled)
        self.assertTrue(SourceRecord.objects.filter(pk=source.pk).exists())
        feature = SyncJob.objects.get(cursor_json__enrichment='spotify-audio-features')
        self.assertEqual(feature.connection.service_id, self.spotify.pk)
        self.assertEqual(feature.connection.auth_type, 'oauth')
        self.assertEqual(feature.cursor_json['seed_profile_link_id'], str(self.profile.pk))

    def test_legacy_enabled_shazam_job_never_dispatches_even_with_force(self):
        from provenance.collectors.runner import run_due
        service, _ = Service.objects.get_or_create(slug='shazam', defaults={'name': 'Shazam'})
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=service, status='active')
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        SyncJob.objects.create(connection=conn, job_type='daily_public_platform', enabled=True,
            cursor_json={'seed_profile_link_id': str(self.profile.pk)})
        with patch('provenance.collectors.runner.collect', side_effect=AssertionError('unexpected dispatch')):
            result = run_due(force=True)
        self.assertEqual(result['requests'], 0)
        self.assertFalse(SyncRun.objects.exists())
