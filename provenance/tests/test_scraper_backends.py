import os
from unittest.mock import patch
from django.test import SimpleTestCase
from provenance.collectors.http import Budget, CollectionError, omkar_client


class ScraperBackendTests(SimpleTestCase):
    def test_self_hosted_uses_configured_origin_without_hosted_key(self):
        with patch.dict(os.environ, {'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SCRAPER_SPOTIFY_URL':'http://127.0.0.1:18100'},clear=True), patch('provenance.collectors.http.JsonClient') as client:
            omkar_client('spotify',Budget(2))
        client.assert_called_once()
        self.assertEqual(client.call_args.args[:2],('http://127.0.0.1:18100',{}))

    def test_hosted_mode_retains_fixed_origin_and_key(self):
        with patch.dict(os.environ,{'OMKAR_API_KEY':'test-key'},clear=True),patch('provenance.collectors.http.JsonClient') as client:
            omkar_client('spotify',Budget(2))
        self.assertEqual(client.call_args.args[:2],('https://spotify-scraper.omkar.cloud',{'API-Key':'test-key'}))

    def test_missing_self_hosted_url_cannot_fall_back_to_hosted(self):
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','OMKAR_API_KEY':'test-key'},clear=True),patch('provenance.collectors.http.JsonClient') as client:
            with self.assertRaisesRegex(CollectionError,'missing_scraper_url'):
                omkar_client('spotify',Budget(2))
        client.assert_not_called()

    def test_bad_mode_and_origin_fail_without_exposing_values(self):
        from provenance.collectors.backends import scraper_backend
        bad_urls=['https://user:secret@host.example','http://public.example:8000',
            'http://127.0.0.1:18100/path','http://127.0.0.1:18100?secret=x',
            'file:///tmp/source','http://127.0.0.1:99999','https://host.example/#fragment']
        for url in bad_urls:
            with self.subTest(url=url),patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SCRAPER_SPOTIFY_URL':url},clear=True):
                with self.assertRaisesRegex(CollectionError,'^invalid_scraper_url$'):
                    scraper_backend('spotify')
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'unknown'},clear=True):
            with self.assertRaisesRegex(CollectionError,'^invalid_scraper_backend$'):
                scraper_backend('spotify')

    def test_backend_switch_is_part_of_retry_fingerprint(self):
        from provenance.collectors.runner import profile_fingerprint
        with patch.dict(os.environ,{},clear=True):
            hosted=profile_fingerprint('spotify','https://open.spotify.com/artist/artist')
        with patch.dict(os.environ,{'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SCRAPER_SPOTIFY_URL':'http://127.0.0.1:18100'},clear=True):
            own=profile_fingerprint('spotify','https://open.spotify.com/artist/artist')
        self.assertNotEqual(hosted,own)

    def test_pinned_revision_changes_cache_identity_and_invalid_pin_is_safe(self):
        from provenance.collectors.backends import scraper_backend
        env={'ARTIST_SCRAPER_MODE':'self_hosted','ARTIST_SCRAPER_SPOTIFY_URL':'http://127.0.0.1:18100'}
        with patch.dict(os.environ,env,clear=True):
            first=scraper_backend('spotify').identity
            with patch.dict(os.environ,{'ARTIST_SCRAPER_SPOTIFY_REVISION':'a'*40}):
                self.assertNotEqual(first,scraper_backend('spotify').identity)
            with patch.dict(os.environ,{'ARTIST_SCRAPER_SPOTIFY_REVISION':'secret-in-invalid-pin'}):
                with self.assertRaisesRegex(CollectionError,'^invalid_scraper_backend$'):
                    scraper_backend('spotify')

    def test_backend_check_does_not_probe_without_flag_or_expose_origins(self):
        import json
        from io import StringIO
        from django.core.management import call_command
        from provenance.collectors.backends import SCRAPER_PLATFORMS
        env={'ARTIST_SCRAPER_MODE':'self_hosted',**{'ARTIST_SCRAPER_'+p.upper().replace('-','_')+'_URL':'http://127.0.0.1:18100' for p in SCRAPER_PLATFORMS}}
        out=StringIO()
        with patch.dict(os.environ,env,clear=True),patch('provenance.management.commands.check_scraper_backends.JsonClient') as client:
            call_command('check_scraper_backends',stdout=out)
        client.assert_not_called()
        self.assertEqual(len(json.loads(out.getvalue())['backends']),8)
        self.assertNotIn('127.0.0.1',out.getvalue())
