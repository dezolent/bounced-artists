#!/usr/bin/env python3
"""Finite cron supervisor for private, pinned scraper processes (no Django import)."""
import argparse
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

APP_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = APP_ROOT / 'deploy/scrapers/sources.json'
PROVIDER_HOME = Path('/opt/provider-python')
SOURCE_ROOT = Path('/opt/scrapers')
SOCIAL_PROVIDERS = frozenset({'youtube', 'instagram', 'tiktok'})
# Only names read by the pinned sources' config.py modules. Optional upstream
# tokens are intentional; Django's official Spotify client credentials are not.
PROVIDER_ENVIRONMENT = {
    'spotify': frozenset({'SPOTIFY_PROXY'}),
    'apple_music': frozenset({'APPLE_MUSIC_PROXY', 'APPLE_MUSIC_TOKEN'}),
    'youtube': frozenset({'YOUTUBE_PROXY'}),
    'soundcloud': frozenset({'SOUNDCLOUD_PROXY', 'SOUNDCLOUD_CLIENT_ID'}),
    'tiktok': frozenset({'TIKTOK_PROXY'}),
    'instagram': frozenset({'INSTAGRAM_PROXY'}),
}


class RuntimeFailure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class InterruptedRun(Exception):
    def __init__(self, signum):
        self.signum = signum


def exit_code(code):
    return code if code >= 0 else 128 - code


def healthy(port, revision):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
    try:
        connection.request('GET', '/__bounced_health')
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read(256)) == {'revision': revision}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def stop_children(children):
    # Kill process groups, including descendants whose direct parent has exited.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def run_runtime(sources, *, provider_home=PROVIDER_HOME, source_root=SOURCE_ROOT,
                command=None, environment=None, ready_timeout=60, health_only=False):
    """Start all providers, verify imports/health, run the bounded existing sync."""
    environment = dict(os.environ if environment is None else environment)
    provider_python = provider_home / 'bin/python3.12'
    if not provider_python.is_file():
        raise RuntimeFailure('Isolated Python 3.12 runtime is missing')
    # Streaming readiness is mandatory; a broken social source must not block it.
    all_sources = sources
    available, unavailable = [], set()
    for source in sources:
        directory = source_root / source['platform']
        try:
            try:
                revision = (directory / '.source-revision').read_text().strip()
            except OSError:
                raise RuntimeFailure(f"Missing pinned source: {source['platform']}") from None
            if revision != source['revision'] or not (directory / 'LICENSE').is_file():
                raise RuntimeFailure(f"Invalid pinned source: {source['platform']}")
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', source['port']))
        except (RuntimeFailure, OSError):
            if source['platform'] not in SOCIAL_PROVIDERS or health_only:
                raise
            unavailable.add(source['platform'])
            continue
        available.append(source)
    sources = available
    children = []
    previous_handlers = {}

    def interrupted(signum, _frame):
        raise InterruptedRun(signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        for source in sources:
            # Provider code does not receive Django/database/storage credentials or
            # an Omkar hosted key. Preserve its own documented optional settings.
            allowed = PROVIDER_ENVIRONMENT[source['platform']]
            child_env = {key: value for key, value in environment.items()
                         if key in ('HOME', 'LANG', 'TZ', 'PATH', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
                         or key in allowed}
            child_env.update(PYTHONHOME=str(provider_home),
                             LD_LIBRARY_PATH=str(provider_home / 'lib'),
                             PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
                             PORT=str(source['port']))
            try:
                children.append(subprocess.Popen(
                    [str(provider_python), str(APP_ROOT / 'deploy/scrapers/serve.py'), str(source['port'])],
                    cwd=source_root / source['platform'], env=child_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
            except OSError:
                if source['platform'] not in SOCIAL_PROVIDERS or health_only:
                    raise
                unavailable.add(source['platform'])
                children.append(None)
        deadline = time.monotonic() + ready_timeout
        pending = {index for index, child in enumerate(children) if child is not None}
        while pending:
            for index, child in enumerate(children):
                if child is not None and child.poll() is not None:
                    if sources[index]['platform'] in SOCIAL_PROVIDERS and not health_only:
                        unavailable.add(sources[index]['platform'])
                        pending.discard(index)
                        continue
                    raise RuntimeFailure(f"Provider exited during startup: {sources[index]['platform']}",
                                         exit_code(child.returncode) or 1)
            for index in list(pending):
                source = sources[index]
                if healthy(source['port'], source['revision']):
                    pending.remove(index)
            if pending and time.monotonic() >= deadline:
                if health_only or any(sources[index]['platform'] not in SOCIAL_PROVIDERS for index in pending):
                    raise RuntimeFailure('Private provider readiness timed out')
                unavailable.update(sources[index]['platform'] for index in pending)
                pending.clear()
            if pending:
                time.sleep(0.1)
        ready_count = sum(source['platform'] not in unavailable for source in sources)
        print(f'Self-hosted scraper runtime ready ({ready_count} private providers).', flush=True)
        if health_only:
            return 0
        environment['ARTIST_SCRAPER_MODE'] = 'self_hosted'
        environment['ARTIST_SCRAPER_UNAVAILABLE'] = ','.join(sorted(unavailable))
        for header, variable in (('X-RapidAPI-Host', 'X_RAPIDAPI_HOST'),
                                 ('X-RapidAPI-Key', 'X_RAPIDAPI_KEY')):
            if header in environment and not environment.get(variable):
                environment[variable] = environment[header]
        environment.setdefault('ARTIST_SYNC_REQUEST_LIMIT', '24000')
        environment.setdefault('ARTIST_SYNC_CATALOG_ENRICHMENT', '1')
        for source in all_sources:
            environment[f"ARTIST_SCRAPER_{source['platform'].upper()}_URL"] = f"http://127.0.0.1:{source['port']}"
            environment[f"ARTIST_SCRAPER_{source['platform'].upper()}_REVISION"] = source['revision']
        sync = subprocess.Popen(command or ['sh', 'scripts/sync_artist_platforms.sh'],
                                cwd=APP_ROOT, env=environment, start_new_session=True)
        providers = list(children)
        children.append(sync)
        while sync.poll() is None:
            for index, child in enumerate(providers):
                if child is not None and child.poll() is not None:
                    if sources[index]['platform'] in SOCIAL_PROVIDERS:
                        unavailable.add(sources[index]['platform'])
                        continue
                    raise RuntimeFailure(f"Provider exited during sync: {sources[index]['platform']}",
                                         exit_code(child.returncode) or 1)
            time.sleep(0.1)
        if unavailable:
            print(json.dumps({'level': 'error', 'event': 'social_provider_unavailable',
                'message': 'Social provider unavailable', 'platforms': sorted(unavailable)}), flush=True)
        return exit_code(sync.returncode) or bool(unavailable)
    except InterruptedRun as exc:
        return 128 + exc.signum
    finally:
        # Ignore a second termination signal while bounded cleanup is in progress.
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        stop_children([child for child in children if child is not None])
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health-check-only', action='store_true',
                        help='Import and health-check providers without contacting platforms or the database.')
    args = parser.parse_args()
    try:
        return run_runtime(json.loads(MANIFEST.read_text()), health_only=args.health_check_only)
    except (RuntimeFailure, OSError) as exc:
        # No exception details: upstream errors can contain credentials or payloads.
        detail = str(exc) if isinstance(exc, RuntimeFailure) else 'runtime_io_error'
        print(json.dumps({'level': 'error', 'event': 'runtime_failed',
                          'message': 'Self-hosted scraper runtime stopped', 'error_code': detail}), file=sys.stderr, flush=True)
        return exc.code if isinstance(exc, RuntimeFailure) else 1


if __name__ == '__main__':
    sys.exit(main())
