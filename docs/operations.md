# Operations

Canonical commands and environment/deployment reference. See [status](status.md)
for dated evidence; use [architecture](database-architecture.md) for domain/access
rules and [the import contract](dezolent-import-plan.md) for preservation rules.

## Local development

Python 3.14; dependencies pinned with hashes in `requirements.txt`. Follow the
[README quick start](../README.md#quick-start) for initial setup. Compose runs
PostgreSQL 18 on loopback port 55432, with a persistent development-only volume.
`docker compose down` preserves it; `down -v` destroys local data.

Django reads process environment, not `.env`. If customization is needed, create
`.env` once from `.env.example`, then export it in your trusted local shell:

```sh
set -a
. ./.env
set +a
```

Never copy production connection values locally. Local settings reject remote DB
hosts and host/service overrides. Test settings use UTF-8 with `template0` and a
separate Django-created `test_…` database; the local role needs CREATEDB.
Make targets force local/test settings even if the caller exported production
settings. They still consume `DATABASE_URL`; its loopback guard must pass.

## Seed the complete local catalog

`make seed` creates artist/reference definitions only. It does not load recordings,
releases or website content. For a populated local workspace, use the existing
Dezolent export, then explicit catalog assignments and optional local file copying.
These steps leave production and public approval state untouched.

```sh
make seed
make import-dry BUNDLE=/Users/josephhill/GitHub/dezolent.com/exports/bounced-artists
# Apply only after the summary reports zero conflicts:
make import-apply BUNDLE=/Users/josephhill/GitHub/dezolent.com/exports/bounced-artists
make import-dry BUNDLE=/Users/josephhill/GitHub/dezolent.com/exports/bounced-artists
```

The final dry run must report zero creates, updates and conflicts. Substitute your
actual export path if different. Artist identities/UUIDs and source snapshots are
preserved; this is not `loaddata` and never resets the database.

Next use the local artist's existing UUID (visible in its Admin URL). Dry-run the
assignment command, inspect the private report and repeat with the returned hash:

```sh
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py assign_import_catalog --website dezolent.com --artist <existing-local-artist-uuid> --report artifacts/local-grants.json
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py assign_import_catalog --website dezolent.com --artist <existing-local-artist-uuid> --report artifacts/local-grants.json --apply --expected-sha256 <reviewed-grants-sha256>
```

Assignments come from persisted import UUID mappings, including mapped remote
assets. They do not come from credits and do not create user memberships. Staff
can inspect local records globally; non-staff users also need an explicit artist
membership in Admin.

To copy the real source files for offline artwork/previews/downloads, run:

```sh
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py import_local_media --bundle /Users/josephhill/GitHub/dezolent.com/exports/bounced-artists --source-root /Users/josephhill/GitHub/dezolent.com --website dezolent.com --artist <existing-local-artist-uuid> --report artifacts/local-media.json
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py import_local_media --bundle /Users/josephhill/GitHub/dezolent.com/exports/bounced-artists --source-root /Users/josephhill/GitHub/dezolent.com --website dezolent.com --artist <existing-local-artist-uuid> --report artifacts/local-media.json --apply --expected-sha256 <reviewed-media-sha256>
```

The media command requires local settings, loopback PostgreSQL and filesystem
storage. It validates bundle digests, path containment, every file's size/hash,
asset ownership and quota before applying. It refuses conflicting editorial files
or checksums. Images get sanitized WebP previews; SVG/JSON/fonts and other verified
source attachments are private downloads, never inline/public previews. Files go
under ignored `artifacts/media/`; repeat runs skip existing matching versions.
Remote source references remain references; the command makes no network requests.

## Commands and verification

`PYTHON` defaults to `.venv/bin/python`. Commands below are run at the repository
root. Make wraps existing Django operations; it neither installs dependencies
nor loads `.env` nor deploys anything.

| Command | Effect |
| --- | --- |
| `make check` | Django checks and migration drift check |
| `make test` | All discovered tests on isolated local PostgreSQL test DB |
| `make test-import BUNDLE=/absolute/export/path` | Require export directory; run importer integration tests |
| `make verify` | Checks, full suite, regenerate schema docs and reject tracked drift |
| `make schema` | Regenerate dictionary and ER views after model/generator edits |
| `make schema-check` | Regenerate and compare generated docs with Git (expected to fail for intentional uncommitted generated changes) |
| `make static` | Collect Admin static files using dummy-DB build settings |
| `make build` | Build the production Dockerfile locally |
| `make seed` | Idempotent artists and service/society references in local DB |
| `make run` | Local server at `127.0.0.1:8000` |

If port 8000 is occupied, use `make run RUN_ADDRESS=127.0.0.1:8001`; do not stop
another developer's running server just to exercise this wrapper.

`make test` discovers bundle tests but they skip if the export is unavailable.
Use `make test-import` whenever changing the importer; it refuses a missing bundle.
Use `DEZOLENT_IMPORT_BUNDLE=/absolute/export/path make test` for the complete suite
with an explicitly located bundle. On 2026-10-20 this suite had 53 tests.
Do not interpret skipped integration tests as full importer validation.

CI (`.github/workflows/ci.yml`) installs hashed dependencies, collects static,
checks Django/migration drift, applies migrations/seeds, checks generated schema,
runs tests against PostgreSQL 18, then builds Docker. The private export is not
committed, so CI cannot replace local full-bundle testing. Reuse a passing check
for the same tree; rerun only checks relevant to changed files or unresolved risks.
For documentation/tooling-only changes, check links, command execution, generated
output and focused diffs rather than repeatedly running unrelated application tests.

Dependency maintenance (deliberate updates only):

```sh
pip-compile --strip-extras --generate-hashes --output-file=requirements.txt requirements.in
.venv/bin/python -m pip install --require-hashes -r requirements.txt
```

## Configuration

| Variable | Meaning |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.settings.local`, `.test`, `.build` or `.production` |
| `DATABASE_URL` | Separate loopback PostgreSQL locally; required private-network URL in production |
| `DJANGO_SECRET_KEY` | Random production secret of at least 50 characters; optional local override |
| `DJANGO_ALLOWED_HOSTS` | Exact comma-separated production hosts, including `healthcheck.railway.app` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Exact comma-separated HTTPS production origins |
| `PORT` | Railway-assigned listening port; container fallback 8000 |
| `WEB_CONCURRENCY` | Gunicorn workers, default 2 |
| `R2_ACCOUNT_ID`, `R2_BUCKET_NAME` | Optional private media destination; requires both access-key variables |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Bucket-scoped R2 credentials, set directly in Railway; never print or commit |
| `DEZOLENT_IMPORT_BUNDLE` | Test bundle directory; importer CLI separately requires `--bundle` |

Production requires configuration and disables debug. Secure cookies, HTTPS
redirects and Railway proxy handling are enabled. Only `/health/` and `/ready/`
are exempt from HTTPS redirects for internal probes. HSTS applies to the current
hostname; include-subdomains/preload are deliberately off (`security.W005/W021`
are silenced) because other subdomains have independent operators. No CORS
permissions are enabled. Build settings have a dummy DB and may only collect static.

## Railway resources

Use these identifiers directly with the Railway plugin; do not repeatedly list
all account resources. Verify the target immediately before a production mutation.
Values below identify resources, not credentials.

| Resource | Identifier |
| --- | --- |
| GitHub | `dezolent/bounced-artists` (existing public repository) |
| Railway project / name | `ac618af0-6c17-423c-a810-b4225e0f6606` / Bounced Artists |
| Production environment | `db7595ad-e582-4d7b-b2e6-4b82121964d5` |
| Web service | `63df2eba-ff47-48aa-a750-f1821e274bff` / bounced-artists-web |
| PostgreSQL service | `e4887d66-9653-4bd5-a4de-a03a943c1fa3` / bounced_artists |
| Region | `us-west2` for both services |
| Known application URL | <https://server-production-381c.up.railway.app/admin/> |
| Intended custom domain | `artists.bounced.studio`; see status before routing work |

The production `DATABASE_URL` references `${{bounced_artists.DATABASE_URL}}` for
private networking. Preserve the actual database name, volume and credentials.
Secrets belong directly in Railway variables, never Git, build args, logs, report
files or shell arguments. Do not retrieve variable values just to inspect config.

## Deployment and migrations

Use the GitHub plugin for remote repository/CI inspection and the Railway plugin
for infrastructure and deployments.
Work directly on `main`; the user commits and pushes manually. Do not create a
feature branch or PR, commit, push, or reconnect production to another branch.
Production follows `main`.
Keep repository visibility and existing integration workflow unchanged.

The lifecycle configuration record is [railway-web-service.json](../deploy/railway-web-service.json),
not an auto-applied IaC file. Docker installs locked dependencies and collects
static using build settings. Railway runs `sh scripts/predeploy.sh` once per
deployment: deployment checks, committed migrations, idempotent artist/reference
seeds. A failure stops rollout. `sh scripts/start.sh` only starts Gunicorn, as a
non-root user binding assigned `PORT`; worker startup never runs migrations.

Review additive migration plans, verify a completed backup and serialize deploys.
After deployment inspect bounded logs and actual `/ready/` response. A successful
build is not proof of database connectivity or successful import. Code rollback
does not reverse database migrations. Never flush, fake migrations or recreate
production to recover a rollout.

Daily DB volume backups were enabled on Pro (every 24 hours, six-day retention).
Backup storage is billed incrementally. Check schedule/completion before a risky
operation. PITR is off and no restore drill has been verified; use an isolated
destination for any future drill. Do not change the plan or provision resources
without authorization.

## Import workflow and bounded output

First read the export's README, MAPPING, manifest and validation summaries, then
parse JSON/JSONL with code. Keep raw exports out of this public repository. Source
snapshots, field hashes, UUID mappings and the import receipt are already persisted
by the importer; copying export files alone is not database population.

Local workflow (full JSON reports stay in ignored `artifacts/`):

```sh
make import-dry BUNDLE=/absolute/export/path
# Review artifacts/dezolent-dry-run.json; apply only after zero conflicts.
make import-apply BUNDLE=/absolute/export/path
make import-dry BUNDLE=/absolute/export/path
```

Each target prints one JSON summary: mode, total creates/updates, conflict count,
source-snapshot count and manifest hash. For per-model counts inspect selected
report fields programmatically. Detailed conflicts stay in the report; existing
ReviewItem tables hold shared-identifier review work. Artifacts may include private
metadata: retain locally, do not commit/upload them indiscriminately. Reports are
overwritten per mode; use `REPORT_DIR=artifacts/unique-run` to retain separate runs.
A failed command exits nonzero; do not interpret an empty/partial report as success.

Summarize an existing report without connecting to any database:

```sh
.venv/bin/python scripts/import_summary.py artifacts/dezolent-dry-run.json
```

For an authorized production import, use the **existing web container** in Railway
Console. Securely transfer the bundle, compare archive hash with the local artifact,
extract outside the application source, then run these existing commands using the
actual absolute runtime path (the example path is not pre-provisioned):

```sh
cd /app
python manage.py showmigrations --plan
python manage.py import_dezolent --bundle /tmp/dezolent-import/dezolent-bundle
# After clean validation, explicit apply:
python manage.py import_dezolent --bundle /tmp/dezolent-import/dezolent-bundle --apply
python manage.py import_dezolent --bundle /tmp/dezolent-import/dezolent-bundle
```

Do not use local Make import targets for production. Verify the target, backup,
migrations and dry-run counts first. Apply is atomic and refuses editorial
conflicts; the repeat dry run must have no creates/updates/conflicts. Read back
receipt/hash/counts from PostgreSQL, not merely terminal command submission.
Container files are ephemeral; source snapshots persist in PostgreSQL. There is
no generic ingestion command, public export CLI, scraper or publishing worker.

## Artist workspace rollout

The root path serves the portal login; `/admin/` remains a staff tool. Review the
working tree and commit/push manually. Production follows `main`; its existing
predeploy command runs the additive migrations. Verify a completed backup before
rollout, then check `/ready/`, `/`, hashed CSS and an actual interactive login.
The custom hostname is explicitly added to allowed hosts and CSRF origins by the
production settings without replacing the configured existing hosts.

Accounts need actual non-staff `ArtistMembership` rows and explicit
`ArtistResourceGrant` assignments. Superusers manage membership; staff manage
same-organization resource assignments. Credits and organization membership never
grant access. No automatic production membership assignment is performed.

For the imported website, the bootstrap command reads persisted stable UUID
mappings. Run dry first, review the private JSON report, then apply only the exact
returned SHA-256. Substitute the actual existing artist UUID; do not match by
title, credit or ISRC. In the authorized production web container:

```sh
python manage.py assign_import_catalog --website dezolent.com --artist <existing-artist-uuid> --report /tmp/catalog-grants.json
python manage.py assign_import_catalog --website dezolent.com --artist <existing-artist-uuid> --report /tmp/catalog-grants.json --apply --expected-sha256 <reviewed-sha256>
```

The command never creates users or memberships, and preserves existing grants.
Repeated apply creates no duplicate assignments. It prints counts/hash, not source
payloads. The report is private operator evidence, not a public artifact.

Production media needs a private R2 bucket and all four `R2_*` variables listed
above, entered directly into Railway. Use bucket-scoped object read/write
credentials; keep `r2.dev` and bucket-domain public access disabled. Partial
configuration fails closed; absent configuration keeps uploads disabled. Images
produce metadata-free WebP derivatives; originals remain authenticated downloads.
The 5 GiB artist quota includes historical versions. Imported references are not
uploaded files and repository-only references show a missing-file state.

Owners/editors request review from the dashboard. Staff first reconcile source
assertions, mark eligible records/matches/rights approved in Admin, then open the
artist data page's public review screen. Review its exact allowlisted preview
before approval; changed content invalidates that preview. Revoke there to remove
API access. This workflow never enqueues website delivery, edits Astro projects or
calls Netlify.

## Administrator bootstrap

An operator-created production superuser and login were verified previously;
seeds never create credentials. For another authorized administrator, open the
existing web service's Railway Console and run:

```sh
cd /app
python manage.py createsuperuser
```

The user enters and submits the hidden password interactively; never send it in
chat, commit it or pass it on a command line. Railway CLI SSH is an alternative
only if installed and already authenticated; it is not a project prerequisite.
Artist accounts stay non-staff and get explicit artist memberships. Verify an
actual login before claiming administrator onboarding succeeded.
