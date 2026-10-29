"""Supervisor contracts using fixture processes; no platform or DB requests."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location(
    'scraper_runtime', Path(__file__).resolve().parents[2] / 'scripts/self_hosted_sync.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class ScraperRuntimeTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'python'
        (self.home / 'bin').mkdir(parents=True)
        (self.home / 'bin/python3.12').touch()
        self.source = {'platform': 'apple_music', 'revision': 'a' * 40, 'port': 18101}
        source_dir = self.root / 'apple_music'
        source_dir.mkdir()
        (source_dir / 'LICENSE').write_text('MIT')
        (source_dir / '.source-revision').write_text('a' * 40)
        self.provider = MagicMock(pid=100001)
        self.provider.poll.return_value = None
        self.sync = MagicMock(pid=100002, returncode=7)
        self.sync.poll.return_value = 7
        self.popen = patch.object(runtime.subprocess, 'Popen', side_effect=[self.provider, self.sync]).start()
        self.addCleanup(patch.stopall)
        self.killpg = patch.object(runtime.os, 'killpg').start()
        patch.object(runtime.socket, 'socket').start()
        self.health = patch.object(runtime, 'healthy', return_value=True).start()

    def run_fixture(self, **kwargs):
        return runtime.run_runtime([self.source], provider_home=self.home, source_root=self.root,
                                   environment={'DATABASE_URL': 'private', 'OMKAR_API_KEY': 'private',
                                                'APPLE_MUSIC_PROXY': 'provider-private'}, **kwargs)

    def test_active_manifest_and_provider_environment_exclude_retired_shazam(self):
        import json
        sources = json.loads(runtime.MANIFEST.read_text())
        self.assertEqual(len(sources), 6)
        self.assertNotIn('shazam', {source['platform'] for source in sources})
        self.assertNotIn('musixmatch', {source['platform'] for source in sources})
        self.assertNotIn('shazam', runtime.PROVIDER_ENVIRONMENT)
        self.assertNotIn('musixmatch', runtime.PROVIDER_ENVIRONMENT)

    def test_rapidapi_hyphenated_variables_survive_shell_boundary(self):
        runtime.run_runtime([self.source], provider_home=self.home, source_root=self.root,
            environment={'X-RapidAPI-Host': 'host', 'X-RapidAPI-Key': 'key'})
        env = self.popen.call_args_list[1].kwargs['env']
        self.assertEqual(env['X_RAPIDAPI_HOST'], 'host')
        self.assertEqual(env['X_RAPIDAPI_KEY'], 'key')

    def test_success_preserves_sync_status_and_cleans_both_groups(self):
        self.assertEqual(self.run_fixture(), 7)
        calls = self.popen.call_args_list
        provider_env = calls[0].kwargs['env']
        self.assertNotIn('DATABASE_URL', provider_env)
        self.assertNotIn('OMKAR_API_KEY', provider_env)
        self.assertEqual(provider_env['APPLE_MUSIC_PROXY'], 'provider-private')
        self.assertEqual(provider_env['PYTHONHOME'], str(self.home))
        self.assertEqual(calls[0].kwargs['cwd'], self.root / 'apple_music')
        self.assertEqual(calls[0].kwargs['stderr'], subprocess.DEVNULL)
        self.assertTrue(calls[0].kwargs['start_new_session'])
        sync_env = calls[1].kwargs['env']
        self.assertEqual(sync_env['ARTIST_SCRAPER_MODE'], 'self_hosted')
        self.assertEqual(sync_env['ARTIST_SYNC_REQUEST_LIMIT'], '24000')
        self.assertEqual(sync_env['ARTIST_SYNC_CATALOG_ENRICHMENT'], '1')
        self.assertEqual(sync_env['ARTIST_SCRAPER_APPLE_MUSIC_URL'], 'http://127.0.0.1:18101')
        self.assertEqual(sync_env['ARTIST_SCRAPER_APPLE_MUSIC_REVISION'], self.source['revision'])
        self.assertNotIn('ARTIST_SCRAPER_APPLE_MUSIC_REVISION', provider_env)
        for pid in (self.provider.pid, self.sync.pid):
            self.killpg.assert_any_call(pid, signal.SIGTERM)
            self.killpg.assert_any_call(pid, signal.SIGKILL)

    def test_explicit_operator_limits_and_enrichment_are_preserved(self):
        self.assertEqual(runtime.run_runtime(
            [self.source], provider_home=self.home, source_root=self.root,
            environment={'ARTIST_SYNC_REQUEST_LIMIT': '120',
                         'ARTIST_SYNC_CATALOG_ENRICHMENT': '0'}), 7)
        sync_env = self.popen.call_args_list[1].kwargs['env']
        self.assertEqual(sync_env['ARTIST_SYNC_REQUEST_LIMIT'], '120')
        self.assertEqual(sync_env['ARTIST_SYNC_CATALOG_ENRICHMENT'], '0')

    def test_provider_allowlist_excludes_credentials_and_preserves_sync_environment(self):
        self.source['platform'] = 'spotify'
        (self.root / 'apple_music').rename(self.root / 'spotify')
        private = {
            'DATABASE_URL': 'database-private',
            'OMKAR_API_KEY': 'hosted-private',
            'R2_ACCESS_KEY_ID': 'storage-private',
            'R2_SECRET_ACCESS_KEY': 'storage-private',
            'SPOTIFY_CLIENT_ID': 'official-private',
            'SPOTIFY_CLIENT_SECRET': 'official-private',
            'SPOTIFY_UNEXPECTED_SECRET': 'prefix-private',
            'APPLE_MUSIC_TOKEN': 'other-provider-private',
        }
        environment = {**private, 'SPOTIFY_PROXY': 'intentional-provider-proxy'}
        self.assertEqual(runtime.run_runtime(
            [self.source], provider_home=self.home, source_root=self.root,
            environment=environment), 7)
        provider_env = self.popen.call_args_list[0].kwargs['env']
        for name in private:
            self.assertNotIn(name, provider_env)
        self.assertEqual(provider_env['SPOTIFY_PROXY'], 'intentional-provider-proxy')
        sync_env = self.popen.call_args_list[1].kwargs['env']
        for name, value in environment.items():
            self.assertEqual(sync_env[name], value)

    def test_health_only_never_launches_sync(self):
        self.assertEqual(self.run_fixture(health_only=True), 0)
        self.assertEqual(self.popen.call_count, 1)

    def test_missing_runtime_never_launches(self):
        (self.home / 'bin/python3.12').unlink()
        with self.assertRaises(runtime.RuntimeFailure):
            self.run_fixture()
        self.popen.assert_not_called()

    def test_missing_social_source_does_not_block_streaming_sync(self):
        social = {'platform': 'youtube', 'revision': 'b' * 40, 'port': 18102}
        self.assertEqual(runtime.run_runtime([self.source, social], provider_home=self.home,
            source_root=self.root, environment={}), 7)
        self.assertEqual(self.popen.call_count, 2)
        self.assertIn('youtube', self.popen.call_args_list[1].kwargs['env']['ARTIST_SCRAPER_UNAVAILABLE'])

    def test_social_process_exiting_during_startup_does_not_block_sync(self):
        social = {'platform': 'youtube', 'revision': 'b' * 40, 'port': 18102}
        directory = self.root / 'youtube'
        directory.mkdir()
        (directory / 'LICENSE').write_text('MIT')
        (directory / '.source-revision').write_text('b' * 40)
        failed = MagicMock(pid=100003, returncode=1)
        failed.poll.return_value = 1
        self.popen.side_effect = [self.provider, failed, self.sync]
        self.assertEqual(runtime.run_runtime([self.source, social], provider_home=self.home,
            source_root=self.root, environment={}), 7)
        self.assertIn('youtube', self.popen.call_args_list[2].kwargs['env']['ARTIST_SCRAPER_UNAVAILABLE'])

    def test_wrong_revision_never_launches(self):
        (self.root / 'apple_music/.source-revision').write_text('b' * 40)
        with self.assertRaises(runtime.RuntimeFailure):
            self.run_fixture()
        self.popen.assert_not_called()

    def test_readiness_timeout_stops_provider_and_does_not_sync(self):
        self.health.return_value = False
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'timed out'):
            self.run_fixture(ready_timeout=0)
        self.assertEqual(self.popen.call_count, 1)
        self.provider.wait.assert_called()

    def test_provider_exit_during_sync_stops_sync(self):
        self.provider.poll.side_effect = [None, 3]
        self.provider.returncode = 3
        self.sync.poll.return_value = None
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'during sync') as raised:
            self.run_fixture()
        self.assertEqual(raised.exception.code, 3)
        self.sync.wait.assert_called()

    def test_signal_preserves_conventional_status_and_cleans_children(self):
        self.sync.poll.return_value = None
        with patch.object(runtime.time, 'sleep', side_effect=runtime.InterruptedRun(signal.SIGTERM)):
            self.assertEqual(self.run_fixture(), 143)
        self.sync.wait.assert_called()
        self.provider.wait.assert_called()

    def test_cleanup_escalates_and_reaps_after_grace_period(self):
        self.provider.wait.side_effect = [subprocess.TimeoutExpired('provider', 5), 0]
        runtime.stop_children([self.provider])
        self.killpg.assert_any_call(self.provider.pid, signal.SIGKILL)
        self.assertEqual(self.provider.wait.call_count, 2)

    def test_signal_exit_status_conversion(self):
        self.assertEqual(runtime.exit_code(-signal.SIGTERM), 143)
