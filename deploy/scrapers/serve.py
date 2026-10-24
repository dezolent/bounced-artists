"""Load one pinned upstream route module in its own process and working directory."""
import json
import os
from pathlib import Path
import sys

# The script directory is /app/deploy/scrapers; upstream uses global config/routes.
sys.path.insert(0, os.getcwd())
import bottle
from cheroot import wsgi
import routes  # noqa: F401 — registers only this source's routes


@bottle.get('/__bounced_health')
def health():
    bottle.response.content_type = 'application/json'
    return json.dumps({'revision': Path('.source-revision').read_text().strip()})


if __name__ == '__main__':
    wsgi.Server(('127.0.0.1', int(sys.argv[1])), bottle.default_app(),
                numthreads=4, server_name='bounced-private-scraper').start()
