# Bounced Artists

Artist information backend operated by **Bounced Studio / Dezolent, LLC**.
Django 5.2 LTS, PostgreSQL, Django REST Framework, Gunicorn, WhiteNoise and Docker.
Artists use a styled login and catalog workspace; private APIs enforce artist
membership plus explicit catalog assignments. Staff review approved public API
snapshots independently of private edits.
Public artist websites remain separate Astro/Netlify projects.

Start agent work with [AGENTS.md](AGENTS.md). This README is the entry point;
keep detailed facts in the documents below rather than duplicating them here.

| Need | Authoritative document |
| --- | --- |
| Eight self-hosted scrapers, Railway cron setup, verification and rollback | [Self-hosted scrapers](docs/self-hosted-scrapers.md) |
| Local setup, environment, tests, deploys, imports, admin bootstrap | [Operations](docs/operations.md) |
| Modules, ownership, API access, implemented vs future boundaries | [Architecture](docs/database-architecture.md) |
| Physical tables, constraints, indexes | [Generated dictionary](docs/schema-dictionary.md) |
| Foreign-key relationships | [Generated ER views](docs/entity-relationships.md) |
| Source preservation, import contract, integration boundaries | [Import and integrations](docs/dezolent-import-plan.md) |
| Dated deployment/import evidence, known limitations, next milestone | [Current status](docs/status.md) |

## Quick start

Use Python 3.14 and Docker Compose, with a separate local database.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
docker compose up -d db
# Create once; do not overwrite an existing .env.
cp -n .env.example .env
make check
.venv/bin/python manage.py migrate --settings=config.settings.local
make seed
.venv/bin/python manage.py createsuperuser --settings=config.settings.local
make run
```

The default local settings match Compose on `127.0.0.1:55432`.
For custom local configuration, export the ignored `.env` as described in the
runbook. Django and Make do not automatically load it. Local settings reject
non-loopback PostgreSQL. Open `http://127.0.0.1:8000/` for the artist workspace;
staff administration remains at `/admin/`. Accounts need explicit artist
memberships and catalog grants; the seed does not create credentials or grant
access from credits. Production uploads require private R2 storage configuration.

For real songs, releases, artwork and fanlinks, follow
[complete local catalog seeding](docs/operations.md#seed-the-complete-local-catalog).
`make seed` alone loads artist/reference definitions, not the catalog export.

```sh
make help
make verify
make test-import BUNDLE=/absolute/path/to/bounced-artists-export
make import-dry BUNDLE=/absolute/path/to/bounced-artists-export
```

Imports default to rollback-only dry runs. The Make targets operate on a local
database; production commands and safeguards are in the runbook. Full reports
stay in ignored `artifacts/`, with counts/hashes printed for agent consumption.

Historical design and implementation plans remain under `docs/` for rationale;
they are not current scope or operational instructions.
