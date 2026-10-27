from uuid import uuid4

from django.test import SimpleTestCase

from provenance.tests.test_audio_features import feature_payload


class RapidAudioBatchTests(SimpleTestCase):
    def test_batches_no_more_than_five_and_maps_each_returned_identity(self):
        from provenance.collectors.audio_features import collect_audio_features
        seeds = [{'recording_id': str(uuid4()), 'platform': 'spotify', 'external_id': f'{i:022d}'} for i in range(7)]
        calls = []
        def fetch(path, **params):
            calls.append((path, params))
            ids = params['ids'].split(',')
            return {'audio_features': [{**feature_payload(), 'id': i, 'uri': 'spotify:track:' + i,
                'track_href': 'https://api.spotify.com/v1/tracks/' + i,
                'analysis_url': 'https://api.spotify.com/v1/audio-analysis/' + i} for i in ids]}
        rows = list(collect_audio_features(fetch, seeds))
        self.assertEqual([len(call[1]['ids'].split(',')) for call in calls], [5, 2])
        self.assertTrue(all(call[0] == '/v1/audio-features' for call in calls))
        self.assertEqual([row.external_id for row in rows], [seed['external_id'] for seed in seeds])

    def test_quota_stops_before_extra_request(self):
        from provenance.collectors.audio_features import collect_audio_features
        seeds = [{'recording_id': str(uuid4()), 'platform': 'spotify', 'external_id': f'{i:022d}'} for i in range(11)]
        calls = []
        def fetch(path, **params):
            calls.append(params['ids'])
            return {'audio_features': []}
        list(collect_audio_features(fetch, seeds, reserve_request=lambda: len(calls) < 2))
        self.assertEqual(len(calls), 2)
