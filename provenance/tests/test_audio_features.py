from copy import deepcopy
from uuid import uuid4
from django.test import SimpleTestCase

from provenance.collectors.http import CollectionError

TRACK_ID = '1234567890ABCDEFGHIJKL'


def feature_payload():
    return {**{key: 0.5 for key in ('acousticness', 'danceability', 'energy', 'instrumentalness', 'liveness', 'speechiness', 'valence')},
        'loudness': -8.2, 'key': 11, 'mode': 1, 'tempo': 122.4, 'time_signature': 4,
        'duration_ms': 180000, 'id': TRACK_ID, 'uri': 'spotify:track:' + TRACK_ID,
        'track_href': 'https://api.spotify.com/v1/tracks/' + TRACK_ID,
        'analysis_url': 'https://api.spotify.com/v1/audio-analysis/' + TRACK_ID, 'type': 'audio_features'}


class AudioFeatureTransportTests(SimpleTestCase):
    def seed(self):
        return {'recording_id': str(uuid4()), 'platform': 'spotify', 'external_id': TRACK_ID}

    def test_one_official_features_call_retains_fields_without_analysis_call(self):
        from provenance.collectors.audio_features import collect_audio_features
        calls = []
        payload = {**feature_payload(), 'unexpected': 'discard'}
        def fetch(path, **params):
            calls.append((path, params))
            return {'audio_features': [payload]}
        rows = list(collect_audio_features(fetch, [self.seed()]))
        self.assertEqual(calls, [('/v1/audio-features', {'ids': TRACK_ID})])
        self.assertEqual(rows[0].endpoint, 'spotify-audio-features')
        self.assertEqual(set(rows[0].payload) - {'_catalog_seed'}, set(feature_payload()))

    def test_403_stops_batch_with_explicit_unavailable_receipt(self):
        from provenance.collectors.audio_features import collect_audio_features
        calls = []
        def fetch(path, **params):
            calls.append(path)
            raise CollectionError('http_403')
        rows = list(collect_audio_features(fetch, [self.seed(), self.seed()]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].incomplete)
        self.assertEqual(rows[0].payload['error_code'], 'spotify_audio_features_access_unavailable')

    def test_access_denial_is_shared_between_artist_batches_in_one_invocation(self):
        from provenance.collectors.audio_features import collect_audio_features
        state, calls = {}, []
        def fetch(path, **params):
            calls.append(path)
            raise CollectionError('http_403')
        first = list(collect_audio_features(fetch, [self.seed()], access_state=state))
        second = list(collect_audio_features(fetch, [self.seed()], access_state=state))
        self.assertEqual(len(calls), 1)
        self.assertTrue(first[0].incomplete and second[0].incomplete)
        self.assertEqual(second[0].payload['error_code'], 'spotify_audio_features_access_unavailable')

    def test_invalid_identity_nonfinite_values_types_and_ranges_are_rejected(self):
        from provenance.collectors.audio_features import validated_features
        for field, value in [('id','other'), ('uri', 'spotify:track:other'), ('acousticness', -0.1),
            ('valence', 1.1), ('tempo', float('nan')), ('key', 12), ('key', True), ('mode', 2),
            ('time_signature', 2), ('duration_ms', -1), ('track_href', 'https://evil.test/x'),
            ('analysis_url', 'https://evil.test/x'), ('type', 'track')]:
            with self.subTest(field=field, value=value):
                payload = deepcopy(feature_payload()); payload[field] = value
                with self.assertRaises(CollectionError):
                    validated_features(payload, TRACK_ID)


from django.test import TestCase
from django.utils import timezone
from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import Asset, PlatformObject, Recording, RecordingAsset, RecordingPlatformMatch
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun
from provenance.collectors.providers import Collected


class AudioFeaturePersistenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Customer', slug='feature-customer', bootstrap_key='dezolent', management_status='managed')
        self.spotify, _ = Service.objects.get_or_create(slug='spotify', defaults={'name': 'Spotify'})
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify,
            external_id='artist123', url='https://open.spotify.com/artist/artist123', verification_status='verified')
        self.conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.spotify, status='active')
        self.job = SyncJob.objects.create(connection=self.conn, job_type='daily_public_platform', enabled=True,
            cursor_json={'seed_profile_link_id': str(self.profile.pk), 'enrichment': 'spotify-audio-features'})
        self.run = SyncRun.objects.create(sync_job=self.job)
        self.song = Recording.objects.create(organization=self.org, title='Existing recording')
        self.grant = ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song)
        self.obj = PlatformObject.objects.create(service=self.spotify, object_type='track', external_id=TRACK_ID)
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.obj, review_status='accepted')
        SourceRecord.objects.create(organization=self.org, service=self.spotify, collector='omkar', collection_method='scraper',
            object_type='track-details', external_id=TRACK_ID, payload_hash='a' * 64, observed_at=timezone.now(),
            payload_json={'id': TRACK_ID, 'artists': [{'id': 'artist123'}]})
        self.payload = {**feature_payload(), '_catalog_seed': {'recording_id': str(self.song.pk), 'platform': 'spotify', 'external_id': TRACK_ID}}

    def persist(self, run=None, at=None):
        from provenance.collectors.runner import persist
        return persist(run or self.run, Collected('spotify-audio-features', 'track', TRACK_ID, deepcopy(self.payload)), at or timezone.now())

    def test_daily_request_reservations_are_shared_and_persistent(self):
        from provenance.collectors.runner import reserve_audio_request
        from provenance.models import AuditEvent
        self.assertTrue(all(reserve_audio_request(self.org.pk) for _ in range(10)))
        self.assertFalse(reserve_audio_request(self.org.pk))
        self.assertEqual(AuditEvent.objects.filter(action='audio_features.request_reserved').count(), 10)

    def test_success_is_stored_once_per_identity_and_excluded_on_later_days(self):
        from datetime import timedelta
        from provenance.collectors.audio_features import latest_audio_features
        from provenance.collectors.catalog_services import catalog_seeds
        self.assertEqual(len(catalog_seeds(self.job)), 1)
        self.assertTrue(self.persist())
        source = SourceRecord.objects.get(object_type='spotify-audio-features')
        self.assertEqual((source.collector, source.collection_method), ('musicae-rapidapi', 'api'))
        later = SyncRun.objects.create(sync_job=self.job)
        self.assertFalse(self.persist(later, timezone.now() + timedelta(days=1)))
        self.assertEqual(SourceRecord.objects.filter(object_type='spotify-audio-features').count(), 1)
        self.assertEqual(catalog_seeds(self.job), [])
        self.assertEqual(latest_audio_features(self.song, artist=self.artist), feature_payload())
        self.assertEqual(Recording.objects.count(), 1)

    def test_revocations_and_shared_artist_scope_are_rechecked_for_read_and_write(self):
        from provenance.collectors.audio_features import latest_audio_features
        self.assertTrue(self.persist())
        other = Artist.objects.create(organization=self.org, name='Other', slug='feature-other')
        ArtistResourceGrant.objects.create(artist=other, recording=self.song)
        self.assertIsNone(latest_audio_features(self.song, artist=other))
        self.job.enabled = False; self.job.save()
        self.assertEqual(latest_audio_features(self.song, artist=self.artist), feature_payload())
        self.match.review_status = 'rejected'; self.match.save()
        self.assertIsNone(latest_audio_features(self.song, artist=self.artist))
        with self.assertRaises(CollectionError):
            self.persist()

    def test_invalid_or_unauthorized_payload_is_never_persisted(self):
        self.payload['energy'] = float('inf')
        with self.assertRaises(CollectionError):
            self.persist()
        self.assertFalse(SourceRecord.objects.filter(object_type='spotify-audio-features').exists())
        self.payload = {**feature_payload(), '_catalog_seed': {'recording_id': str(uuid4()), 'platform': 'spotify', 'external_id': TRACK_ID}}
        with self.assertRaises(CollectionError):
            self.persist()

    def test_missing_lyrics_selection_skips_assets_and_existing_text_candidates(self):
        from provenance.collectors.catalog_services import catalog_seeds
        service, _ = Service.objects.get_or_create(slug='musixmatch', defaults={'name': 'Musixmatch'})
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=service, status='active')
        job = SyncJob.objects.create(connection=conn, job_type='daily_public_platform', enabled=True,
            cursor_json={'seed_profile_link_id': str(self.profile.pk)})
        self.assertEqual(len(catalog_seeds(job)), 1)
        asset = Asset.objects.create(organization=self.org, source_repository='existing', source_path='fixture.txt', category='lyrics_text')
        link = RecordingAsset.objects.create(recording=self.song, asset=asset, purpose='lyrics')
        self.assertEqual(catalog_seeds(job), [])
        link.delete()
        SourceRecord.objects.create(organization=self.org, service=service, collector='omkar', collection_method='scraper',
            object_type='musixmatch-track-lyrics', external_id='77', payload_hash='c' * 64, observed_at=timezone.now(),
            payload_json={'has_lyrics': True, 'lyrics': {'is_restricted': False, 'text': 'Private fixture'},
                '_catalog_seed': {'recording_id': str(self.song.pk), 'platform': 'spotify', 'external_id': TRACK_ID}})
        self.assertEqual(catalog_seeds(job), [])

    def test_runner_dispatches_only_official_api_then_skips_success_on_next_run(self):
        from unittest.mock import patch
        from provenance.collectors.runner import run_due
        with patch('provenance.collectors.providers.omkar_client', side_effect=AssertionError('unexpected scraper')), patch(
                'provenance.collectors.providers.rapid_audio_client') as client:
            client.return_value.return_value = {'audio_features': [feature_payload()]}
            first = run_due(force=True)
            second = run_due(force=True)
        self.assertEqual((first['succeeded'], second['succeeded']), (1, 1))
        self.assertEqual(client.return_value.call_count, 1)
        self.assertEqual(SourceRecord.objects.filter(object_type='spotify-audio-features').count(), 1)

    def test_runner_reports_403_access_unavailable_as_failed_with_safe_evidence(self):
        from unittest.mock import patch
        from provenance.collectors.runner import run_due
        with patch('provenance.collectors.providers.rapid_audio_client') as client:
            client.return_value.side_effect = CollectionError('http_403')
            result = run_due(force=True)
        self.assertEqual(result['failed'], 1)
        run = SyncRun.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(run.error_summary, 'spotify_audio_features_access_unavailable')
        self.assertEqual(run.counts_json['endpoint_errors'], {'spotify-audio-features': {'spotify_audio_features_access_unavailable': 1}})
        self.assertTrue(run.counts_json['incomplete'])
        self.assertEqual(SourceRecord.objects.get(object_type='spotify-audio-features-unavailable').payload_json,
            {'endpoint': 'spotify-audio-features', 'error_code': 'spotify_audio_features_access_unavailable'})

    def test_audited_merge_preserves_readable_features_and_one_time_skip(self):
        from provenance.collectors.audio_features import latest_audio_features
        from provenance.collectors.catalog_services import catalog_seeds
        from provenance.models import AuditEvent
        self.assertTrue(self.persist())
        original_payload = deepcopy(SourceRecord.objects.get(object_type='spotify-audio-features').payload_json)
        canonical = Recording.objects.create(organization=self.org, title='Canonical recording')
        self.match.recording = canonical; self.match.save()
        self.grant.recording = canonical; self.grant.save()
        self.song.archived_at = timezone.now(); self.song.status = 'archived'; self.song.save()
        AuditEvent.objects.create(organization=self.org, action='catalog.reconcile', change_json={
            'recording_merges': [{'duplicate_id': str(self.song.pk), 'canonical_id': str(canonical.pk), 'organization_id': str(self.org.pk)}]})
        self.assertEqual(latest_audio_features(canonical, artist=self.artist), feature_payload())
        self.assertEqual(catalog_seeds(self.job), [])
        self.assertEqual(SourceRecord.objects.get(object_type='spotify-audio-features').payload_json, original_payload)

    def test_batch_reader_validates_seed_authorization_once_per_source_job(self):
        from unittest.mock import patch
        from provenance.collectors.audio_features import latest_audio_features_for_recordings
        from provenance.collectors.catalog_services import catalog_seeds
        self.assertTrue(self.persist())
        with patch('provenance.collectors.catalog_services.catalog_seeds', wraps=catalog_seeds) as authorize:
            result = latest_audio_features_for_recordings([self.song], artist=self.artist)
        self.assertEqual(result, {self.song.pk: feature_payload()})
        self.assertEqual(authorize.call_count, 1)

    def test_hundred_recording_overview_has_bounded_database_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from provenance.collectors.audio_features import latest_audio_features_for_recordings
        recordings = [Recording(organization=self.org, title=f'Fixture {index}') for index in range(100)]
        Recording.objects.bulk_create(recordings)
        objects = [PlatformObject(service=self.spotify, object_type='track', external_id=f'{index:022d}') for index in range(100)]
        PlatformObject.objects.bulk_create(objects)
        ArtistResourceGrant.objects.bulk_create([ArtistResourceGrant(artist=self.artist, recording=row) for row in recordings])
        RecordingPlatformMatch.objects.bulk_create([RecordingPlatformMatch(recording=row, platform_object=obj, review_status='accepted') for row, obj in zip(recordings, objects)])
        sources = []
        for row, obj in zip(recordings, objects):
            identity = obj.external_id
            sources.append(SourceRecord(organization=self.org, service=self.spotify, collector='omkar', collection_method='scraper',
                object_type='track-details', external_id=identity, payload_hash='e' * 64, observed_at=timezone.now(),
                payload_json={'id': identity, 'artists': [{'id': 'artist123'}]}))
            payload = {**feature_payload(), 'id': identity, 'uri': 'spotify:track:' + identity,
                'track_href': 'https://api.spotify.com/v1/tracks/' + identity,
                'analysis_url': 'https://api.spotify.com/v1/audio-analysis/' + identity,
                '_catalog_seed': {'recording_id': str(row.pk), 'platform': 'spotify', 'external_id': identity}}
            sources.append(SourceRecord(organization=self.org, service=self.spotify, sync_run=self.run, collector='spotify-web-api', collection_method='api',
                object_type='spotify-audio-features', external_id=identity, payload_hash='f' * 64, observed_at=timezone.now(), payload_json=payload))
        SourceRecord.objects.bulk_create(sources)
        with CaptureQueriesContext(connection) as queries:
            result = latest_audio_features_for_recordings(recordings, artist=self.artist)
        self.assertEqual(len(result), 100)
        self.assertLessEqual(len(queries), 20)

    def test_two_accepted_native_ids_fetch_only_one_feature_for_the_recording(self):
        from unittest.mock import patch
        from provenance.collectors.runner import run_due
        from provenance.collectors.catalog_services import catalog_seeds
        second_id = 'ZZZZZZZZZZZZZZZZZZZZZZ'
        obj = PlatformObject.objects.create(service=self.spotify, object_type='track', external_id=second_id)
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj, review_status='accepted')
        SourceRecord.objects.create(organization=self.org, service=self.spotify, collector='omkar', collection_method='scraper',
            object_type='track-details', external_id=second_id, payload_hash='8' * 64, observed_at=timezone.now(),
            payload_json={'id': second_id, 'artists': [{'id': 'artist123'}]})
        self.assertEqual([seed['external_id'] for seed in catalog_seeds(self.job)], [TRACK_ID])
        with patch('provenance.collectors.providers.rapid_audio_client') as client:
            client.return_value.return_value = {'audio_features': [feature_payload()]}
            self.assertEqual(run_due(force=True)['succeeded'], 1)
            from datetime import timedelta
            tomorrow = timezone.now() + timedelta(days=1)
            with patch('provenance.collectors.runner.timezone.now', return_value=tomorrow):
                self.assertEqual(run_due(force=True)['succeeded'], 1)
        self.assertEqual(client.return_value.call_count, 1)
        self.assertEqual(catalog_seeds(self.job), [])
        self.match.review_status = 'rejected'; self.match.save()
        self.assertEqual([seed['external_id'] for seed in catalog_seeds(self.job)], [second_id])

    def test_runner_shares_app_access_denial_across_jobs_without_hiding_failures(self):
        from unittest.mock import patch
        from provenance.collectors.runner import run_due
        SyncJob.objects.create(connection=self.conn, job_type='daily_public_platform', enabled=True,
            cursor_json=deepcopy(self.job.cursor_json))
        with patch('provenance.collectors.providers.rapid_audio_client') as client:
            client.return_value.side_effect = CollectionError('http_403')
            result = run_due(force=True)
        self.assertEqual(result['failed'], 2)
        self.assertEqual(client.return_value.call_count, 1)
        self.assertEqual(SyncRun.objects.filter(error_summary='spotify_audio_features_access_unavailable').count(), 2)
