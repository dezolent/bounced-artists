# Bounced Artists

Artist information backend operated by **Bounced Studio / Dezolent, LLC**.
Django 5.2 LTS, PostgreSQL, Django REST Framework, Gunicorn, WhiteNoise, and Docker.
The intended custom domain is `artists.bounced.studio`; DNS is a separate milestone.

## Deployed foundation

The production application is available at
<https://server-production-381c.up.railway.app/admin/>.
Railway project **Bounced Artists** runs **bounced-artists-web** and the existing
**bounced_artists** PostgreSQL service in `us-west2`. The existing repository is
public; its visibility was preserved. [PR #1](https://github.com/dezolent/bounced-artists/pull/1)
contains the foundation on `codex/backend-foundation`, which is currently deployed.

Verified on 2026-10-20: Docker build, pre-deployment migrations, three seeded artists,
live liveness/readiness, anonymous API denial, admin login page and hashed static CSS,
secure CSRF cookie and HSTS. All 31 tests passed locally and GitHub CI passed against
PostgreSQL 18, including a Docker build. The operator created the administrator
interactively; authenticated administration and all three managed artist records
were verified in production. No administrator password is shipped.

## Local development

Use Python 3.14 and Docker Compose. The development database is separate from
Railway and listens only on loopback. Its sample credentials are local-only.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.txt
docker compose up -d db
cp .env.example .env
set -a
. ./.env
set +a
python manage.py migrate
python manage.py seed_artists
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/`. Django reads environment variables; it does
not automatically load `.env`. Local settings reject non-loopback database hosts.
Never copy the production database URL into local development. `docker compose
down` preserves the development volume; `down -v` deletes it.

## Configuration

| Variable | Use |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.local` locally, `config.settings.production` on Railway |
| `DATABASE_URL` | PostgreSQL URL; required in production |
| `DJANGO_SECRET_KEY` | Random secret, at least 50 characters; required in production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated exact hostnames; include `healthcheck.railway.app` on Railway |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated exact HTTPS origins in production |
| `PORT` | Railway-assigned listening port; local container fallback is 8000 |
| `WEB_CONCURRENCY` | Gunicorn worker count, default 2 |

Production always disables debug, uses secure cookies, redirects ordinary HTTP
to HTTPS, and trusts Railway's `X-Forwarded-Proto` header. Deploy this configuration
behind Railway's trusted proxy only. HSTS is enabled for this hostname; include-
subdomains and preload are intentionally disabled because other sites have separate
operators/deployment paths. The corresponding two deployment warnings are explicitly
silenced. No CORS permissions are enabled. Build-only settings use a dummy database
and must never be used to serve the application.

## Scope and access

- `core`: custom `User` model and health/readiness probes.
- `artists`: UUID artist records, managed/unmanaged status, biographies, location,
  optional IANA timezone, memberships, aliases, contacts, platforms, and profile links.
- `config`: configuration, routing, and WSGI entry point.
- Django Admin: searchable/filterable artist records and related data.
- `seed_artists`: creates missing Dezolent / `dezolent`, SRY4U / `sry4u`, and
  Morbeato / `morbeato` as managed artists. Existing records are preserved, including
  names, slugs, and edited management status. An internal immutable bootstrap key
  preserves seed identity after slug changes. No biographical/contact/profile data is invented.

| Account/role | Current permissions |
| --- | --- |
| Anonymous or inactive | No artist API or administrative access |
| Viewer | Read artists to which the user belongs |
| Editor | Same scoped read access in this foundation; editing API is deferred |
| Owner | Same scoped read access in this foundation; membership API is deferred |
| Active staff | Global artist API read access; Admin operations require model permissions |
| Superuser | Global administration, including users and memberships |

Staff are trusted global operators. Artist users must remain non-staff. Django model
permissions alone never bypass artist API membership filtering. Detail lookups use
the filtered queryset, returning 404 for another artist's UUID. Only superusers may
administer users/memberships; group administration is not exposed. Membership roles
do not grant staff status or model permissions. Future write APIs should give editors
content editing and owners membership management, with explicit scoped authorization
and tests before enabling either capability.

Contacts default to staff-only visibility. Even contacts marked public are not
published yet. The current serializer intentionally omits all contacts, memberships,
and profile links; adding fields requires an explicit privacy review.

## Endpoints

| Path | Behavior |
| --- | --- |
| `/health/` | Liveness, no database query; returns only status |
| `/ready/` | `SELECT 1`; 200 when connected, generic 503 on database failure |
| `/admin/` | Staff administration, session authentication and CSRF protection |
| `/api-auth/login/` | Session login for existing accounts; no public registration |
| `/api/v1/artists/` | Authenticated paginated list, filtered by membership |
| `/api/v1/artists/<uuid>/` | Authenticated scoped detail |

The artist API supports GET/HEAD/OPTIONS only. Use the session login page and then
the API on the same origin. Unsafe methods are rejected. Public published-content
endpoints will be a separate explicit projection, never an anonymous version of
these management serializers.

## Checks and commands

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test -v 2
python manage.py seed_artists
python manage.py collectstatic --noinput
docker build -t bounced-artists .
```

Tests create and destroy a separate test database on the local PostgreSQL server;
the local role needs CREATEDB. CI runs against its own PostgreSQL 18 service, then
builds the Docker image. `requirements.txt` pins transitive dependencies with hashes.
To update it deliberately, install `pip-tools`, then run:

```sh
pip-compile --strip-extras --generate-hashes --output-file=requirements.txt requirements.in
pip install --require-hashes -r requirements.txt
```

## Railway deployment

Reuse project **Bounced Artists**, environment **production**, its existing web
service (ID `63df2eba-ff47-48aa-a750-f1821e274bff`), and PostgreSQL service
**bounced_artists**. Keep both in `us-west2`. The web service is named
`bounced-artists-web`. Do not rename the actual PostgreSQL database or replace its volume.

Set the web service's `DATABASE_URL` to the Railway reference
`${{bounced_artists.DATABASE_URL}}`, which uses the database's internal network.
Store a fresh production secret directly in Railway variables; never in Git,
build arguments, shell history, or documentation. Configure the exact Railway
hostname(s) in allowed hosts and HTTPS trusted origins.

Railway service settings select the Dockerfile and `/ready/` health check; their
exact connector/API update payload is versioned in `deploy/railway-web-service.json`.
This file is an operational configuration record, not an automatically evaluated
Railway file. Apply its fields through the Railway connector or service settings
when changing lifecycle configuration. Railway has deprecated `railway.json` and
`railway.toml`; this project does not depend on either. Project-wide IaC ownership
can be adopted separately after importing and reviewing the existing resources.
Docker installs
locked dependencies and collects static files using build-only settings. Railway
runs `sh scripts/predeploy.sh` once per deployment: deployment checks, committed
migrations, then idempotent seeding. A failure stops rollout. Workers only run
`sh scripts/start.sh`; they do not migrate. Gunicorn runs as a non-root user and
binds the assigned port. The container needs no writable persistent volume.

Use additive migrations and review the migration plan before deploying future
schema changes. Serialize production deployments. A code rollback does not undo
database migrations; use forward-compatible migrations and restore only after an
explicit recovery decision. Never use `flush`, fake migrations, or recreate the
production database to resolve a failed rollout.

The initial feature branch is `codex/backend-foundation`. Railway can deploy this
branch while its PR is reviewed; after merge, reconnect the same service to `main`.
Do not leave the service pinned to the failed starter's old commit.

After the operator upgraded Railway to Pro on 2026-10-20, daily volume backups were
enabled for the existing PostgreSQL service: every 24 hours, retained for six days.
An initial on-demand backup completed successfully (103 MB). Backup storage is
billed incrementally under Railway's volume pricing. Confirm ongoing schedule runs
in the Backups tab. Point-in-time recovery remains off, and a restore drill has not
been performed; test recovery in an isolated destination before claiming it verified.

## Administrator bootstrap

No production admin credentials are shipped or created by seeding. In Railway,
open **bounced-artists-web → Console** and run
`cd /app && python manage.py createsuperuser`.
The authenticated browser console supports interactive password entry.
Alternatively, from a trusted interactive terminal with the Railway CLI authenticated
and an authorized SSH key, run this against the deployed web container:

```sh
railway ssh --project ac618af0-6c17-423c-a810-b4225e0f6606 --environment db7595ad-e582-4d7b-b2e6-4b82121964d5 --service 63df2eba-ff47-48aa-a750-f1821e274bff -- sh -c 'cd /app && python manage.py createsuperuser'
```

Enter your username/email and password at the prompts. Password input is hidden.
Do not pass passwords on the command line or save bootstrap credentials as persistent
deployment variables. Verify login at the deployed `/admin/`, then create non-staff
artist accounts and assign memberships. A visible login form is not proof that an
administrator account exists.

## Remaining milestones

Next: assign real artist memberships and verify editorial workflows, then design
a small catalog and explicit publishing contract. Perform an isolated backup
restore drill as an operational follow-up.
Future modules are catalog, integrations, rights, events, analytics, and publishing.
Scrapers, streaming ingestion, MLC/PRO integrations, full rights schema, Netlify
hooks, Astro migrations, custom dashboards, and R2 storage are not implemented.
