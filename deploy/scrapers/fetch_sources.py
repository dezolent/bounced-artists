"""Build-only source acquisition; the manifest is reviewed application source."""
import json
from pathlib import Path
import re
import subprocess
import sys

manifest, destination = map(Path, sys.argv[1:])
for source in json.loads(manifest.read_text()):
    name, revision = source['repository'], source['revision']
    if not re.fullmatch(r'omkarcloud/[a-z-]+-scraper', name):
        raise ValueError('Unexpected source repository')
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Source revision must be a complete commit SHA')
    target = destination / source['platform']
    target.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(target), *args], text=True).strip()
    git('init', '--quiet')
    git('remote', 'add', 'origin', f'https://github.com/{name}.git')
    git('fetch', '--quiet', '--depth=1', 'origin', revision)
    git('checkout', '--quiet', '--detach', 'FETCH_HEAD')
    if git('rev-parse', 'HEAD') != revision:
        raise RuntimeError('Source revision verification failed')
    if not (target / 'LICENSE').is_file() or not (target / 'routes.py').is_file():
        raise RuntimeError('Source license or route module is missing')
    (target / '.source-revision').write_text(revision + '\n')
    # Keep LICENSE and all source; Git metadata is unnecessary in the runtime.
    import shutil
    shutil.rmtree(target / '.git')
