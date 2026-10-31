from django.test import SimpleTestCase


class AudioPresentationTests(SimpleTestCase):
    def profile(self, payloads):
        from portal.audio_presentation import audio_profile
        return audio_profile(payloads)

    def test_radar_excludes_missing_values_and_preserves_zero(self):
        profile = self.profile([{'energy': 0, 'valence': .8, 'loudness': -10}, {'energy': 1, 'loudness': -4}])
        rows = {row['code']: row for row in profile['features']}
        self.assertEqual(rows['energy']['value'], .5)
        self.assertEqual(rows['energy']['count'], 2)
        self.assertEqual(rows['valence']['value'], .8)
        self.assertIsNone(rows['acousticness']['value'])
        self.assertEqual(profile['loudness'], {'average': -7, 'min': -10, 'max': -4, 'count': 2})
        self.assertEqual(profile['coverage'], 2)
        self.assertFalse(profile['polygon'])

    def test_complete_radar_and_musical_stats_use_valid_values(self):
        from portal.audio_presentation import AUDIO_FEATURES
        payload = {code: .5 for code, _ in AUDIO_FEATURES}
        payload.update(key=5, mode=0, tempo=120, time_signature=4, duration_ms=180000)
        profile = self.profile([payload])
        self.assertEqual(len(profile['polygon'].split()), 7)
        self.assertEqual(dict(profile['stats'])['Key'], 'F Minor')
        self.assertEqual(dict(profile['stats'])['Duration'], '3:00')
        self.assertEqual(dict(profile['stats'])['Time signature'], '4/4')

    def test_invalid_features_and_unknown_key_are_not_zero(self):
        profile = self.profile([{'key': -1, 'mode': 0, 'energy': float('nan'), 'valence': 2, 'tempo': True}])
        self.assertEqual(profile['coverage'], 0)
        self.assertEqual(dict(profile['stats'])['Key'], 'Unknown')
        self.assertTrue(all(row['value'] is None for row in profile['features']))


from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from artists.models import Artist, ArtistMembership, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingArtist, RecordingPlatformMatch
from portal.models import OperationRemoval
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun
from provenance.tests.test_audio_features import feature_payload


class AudioOverviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Audio artist', slug='audio-overview', bootstrap_key='dezolent', management_status='managed')
        self.user = get_user_model().objects.create_user(username='audio-overview-viewer')
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role='viewer')
        self.spotify = Service.objects.create(slug='spotify', name='Spotify')
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify, external_id='artist123',
            url='https://open.spotify.com/artist/artist123', verification_status='verified')
        conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.spotify, status='active')
        job = SyncJob.objects.create(connection=conn, job_type='daily_public_platform', cursor_json={
            'seed_profile_link_id':str(self.profile.pk), 'enrichment':'spotify-audio-features'})
        self.run = SyncRun.objects.create(sync_job=job, status='succeeded')

    def add_songs(self, start, count):
        songs = Recording.objects.bulk_create([Recording(organization=self.org, title=f'Song {i}') for i in range(start, start+count)])
        objects = PlatformObject.objects.bulk_create([PlatformObject(service=self.spotify, object_type='track', external_id=f'{i:022d}') for i in range(start, start+count)])
        ArtistResourceGrant.objects.bulk_create([ArtistResourceGrant(artist=self.artist, recording=song) for song in songs])
        RecordingArtist.objects.bulk_create([RecordingArtist(artist=self.artist, recording=song) for song in songs])
        RecordingPlatformMatch.objects.bulk_create([RecordingPlatformMatch(recording=song, platform_object=obj, review_status='accepted') for song, obj in zip(songs, objects)])
        sources = []
        for index, (song, obj) in enumerate(zip(songs, objects), start):
            body = {**feature_payload(), 'id':obj.external_id, 'uri':'spotify:track:'+obj.external_id,
                'track_href':'https://api.spotify.com/v1/tracks/'+obj.external_id,
                'analysis_url':'https://api.spotify.com/v1/audio-analysis/'+obj.external_id,
                'energy':0 if index == 0 else 1,
                '_catalog_seed':{'recording_id':str(song.pk),'platform':'spotify','external_id':obj.external_id}}
            sources.extend([SourceRecord(organization=self.org, service=self.spotify, sync_run=self.run,
                collector='spotify-web-api', collection_method='api', object_type='spotify-audio-features',
                external_id=obj.external_id, observed_at=timezone.now(), payload_hash='a'*64, payload_json=body),
                SourceRecord(organization=self.org, service=self.spotify, collector='omkar', collection_method='scraper',
                object_type='track-details', external_id=obj.external_id, observed_at=timezone.now(), payload_hash='b'*64,
                payload_json={'id':obj.external_id, 'artists':[{'id':'artist123'}]})])
        SourceRecord.objects.bulk_create(sources)
        return songs

    def test_overview_averages_unique_authorized_songs_with_constant_query_count(self):
        from portal.audio_presentation import artist_audio
        self.add_songs(0, 1)
        with CaptureQueriesContext(connection) as small:
            first = artist_audio(self.user, self.artist)
        self.assertEqual(first['coverage'], 1)
        self.add_songs(1, 99)
        with CaptureQueriesContext(connection) as expanded:
            profile = artist_audio(self.user, self.artist)
        self.assertEqual(profile['coverage'], 100)
        self.assertEqual(profile['total_songs'], 100)
        self.assertEqual(next(row['value'] for row in profile['features'] if row['code']=='energy'), .99)
        self.assertEqual(len(expanded), len(small))
        self.assertLessEqual(len(expanded), 20)

    def test_overview_uses_only_active_primary_credits_and_keeps_song_features(self):
        from portal.audio_presentation import artist_audio,recording_audio
        songs=self.add_songs(0,6)
        guest=Artist.objects.create(organization=self.org,name='Primary guest',slug='audio-primary-guest')
        RecordingArtist.objects.create(recording=songs[1],artist=guest,role='primary')
        RecordingArtist.objects.create(recording=songs[1],artist=self.artist,role='featured')
        RecordingArtist.objects.filter(recording=songs[1],artist=self.artist,role='primary').update(role=' Primary ')
        RecordingArtist.objects.filter(recording=songs[2],artist=self.artist).update(role='featured')
        RecordingArtist.objects.create(recording=songs[2],artist=guest,role='primary')
        RecordingArtist.objects.filter(recording=songs[3],artist=self.artist).update(role='guest')
        RecordingArtist.objects.filter(recording=songs[4],artist=self.artist).delete()
        removed=songs[5].credits.get(artist=self.artist)
        OperationRemoval.objects.create(model_label='catalog.recordingartist',object_id=str(removed.pk),removed_by=self.user)
        for song,loudness in zip(songs,[-12,-6,-1,-40,-30,-2]):
            source=SourceRecord.objects.get(object_type='spotify-audio-features',payload_json___catalog_seed__recording_id=str(song.pk))
            source.payload_json['loudness']=loudness
            source.save()
        before=SourceRecord.objects.count()
        profile=artist_audio(self.user,self.artist)
        self.assertEqual(profile['total_songs'],2)
        self.assertEqual(profile['coverage'],2)
        self.assertEqual(next(row['value'] for row in profile['features'] if row['code']=='energy'),.5)
        self.assertTrue(all(row['count']==2 for row in profile['features']))
        self.assertEqual(profile['loudness'],{'average':-9,'min':-12,'max':-6,'count':2})
        detail=recording_audio(self.artist,songs[2])
        self.assertEqual(detail['coverage'],1)
        self.assertEqual(detail['loudness']['average'],-1)
        self.client.force_login(self.user)
        from django.urls import reverse
        response=self.client.get(reverse('portal:overview'),{'artist':self.artist.pk})
        self.assertEqual(response.context['audio_profile']['total_songs'],2)
        response=self.client.get(reverse('portal:recording',args=[self.artist.pk,songs[2].pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Audio features')
        self.assertEqual(SourceRecord.objects.count(),before)

    def test_featured_only_catalog_has_no_artist_average(self):
        from portal.audio_presentation import artist_audio
        song=self.add_songs(0,1)[0]
        song.credits.update(role='featured')
        profile=artist_audio(self.user,self.artist)
        self.assertEqual(profile['total_songs'],0)
        self.assertEqual(profile['coverage'],0)
        self.assertIsNone(profile['loudness'])
        self.assertTrue(all(row['value'] is None for row in profile['features']))
