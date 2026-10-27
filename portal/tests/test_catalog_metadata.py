from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from portal.catalog_metadata import consolidated_metadata


class ConsolidatedMetadataTests(SimpleTestCase):
    def source(self, slug, body, *, official=False, day=1):
        return SimpleNamespace(pk=uuid4(), service=SimpleNamespace(slug=slug, name=slug.title()),
            collector='spotify-web-api' if official else 'omkar', object_type='track-details',
            payload_json=body, observed_at=datetime(2026, 1, day, tzinfo=timezone.utc))

    def song(self, **values):
        return SimpleNamespace(language='', explicit=None, version_title='', duration_ms=None, original_release_date=None, **values)

    def groups(self, *sources):
        return [{'catalog_metadata': [{'source': source} for source in sources]}]

    def test_deterministic_priority_preserves_manual_values_and_zero(self):
        song = self.song()
        song.language, song.duration_ms, song.explicit = 'French', 0, False
        apple = self.source('apple-music', {'language':'German', 'duration_ms':1000, 'explicit':True, 'isrc':'APPLE'})
        spotify = self.source('spotify', {'language':'English', 'duration_ms':2000, 'explicit':True, 'external_ids':{'isrc':'SPOTIFY'}})
        result = consolidated_metadata(song, self.groups(apple, spotify))
        self.assertNotIn('language', result['fallback'])
        self.assertNotIn('duration_ms', result['fallback'])
        self.assertNotIn('explicit', result['fallback'])
        self.assertEqual(next(row['value'] for row in result['rows'] if row['label']=='ISRC'), 'SPOTIFY')
        reversed_result = consolidated_metadata(song, self.groups(spotify, apple))
        self.assertEqual(result['rows'], reversed_result['rows'])

    def test_existing_labels_genres_identifiers_suppress_duplicate_platform_claims(self):
        source = self.source('spotify', {'label':'Source label', 'genres':['Pop'], 'isrc':'SOURCE', 'copyrights':[{'text':'2026 Test'}], 'release_date':'2020'})
        result = consolidated_metadata(self.song(), self.groups(source), has_labels=True, has_genres=True, identifiers=[SimpleNamespace(scheme='ISRC')])
        self.assertEqual([row['label'] for row in result['rows']], ['Copyright'])
        self.assertEqual(result['rows'][0]['value'], '2026 Test')
        self.assertEqual(result['fallback']['date_precision'], 'year')
        self.assertEqual(result['fallback']['date'].year, 2020)

    def test_official_fields_take_priority_and_newest_scraper_snapshot_is_stable(self):
        older = self.source('spotify', {'language':'Old'}, day=1)
        newer = self.source('spotify', {'language':'New'}, day=2)
        official = self.source('spotify', {'language':'Official'}, official=True)
        self.assertEqual(consolidated_metadata(self.song(), self.groups(older, newer))['fallback']['language'], 'New')
        self.assertEqual(consolidated_metadata(self.song(), self.groups(newer, official))['fallback']['language'], 'Official')
