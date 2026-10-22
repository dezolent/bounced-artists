# Working on Bounced Artists

Django 5.2 LTS/PostgreSQL backend for Bounced Studio / Dezolent, LLC: private
artist administration, catalog and source evidence, eventually approved public content.

## Module map

`config` settings/routes; `core` custom user/probes; `artists` organizations,
memberships/private API/importer; `catalog` recordings/releases/assets/platform
matches; `rights` parties/works/source assertions; `events` events/tickets;
`provenance` evidence/review/audit; `publishing` draft pages/SEO/fanlinks/outbox;
`analytics` observation schema. Tables do not imply running integrations.
`portal` owns artist login/screens, scoped editorial CRUD, upload validation,
user administration, staff Operations/review queue and optional-review publication;
`publishing` owns immutable public API projections. Use portal workflows for
operations; generated evidence and audit history remain read-only.

## Tools and context

- Implement authorized changes directly without brainstorming approval gates,
  written specs, or implementation-plan documents. Resolve routine design choices
  from the request and existing code; ask only for genuinely missing requirements
  or authorization for destructive/external actions. This project workflow overrides
  skill defaults that require those review or document steps.
- Prefer local filesystem tools and Git for code: `rg --files <paths>`,
  `rg -n <pattern> <paths>`, bounded `sed` reads, focused `git diff -- <paths>`.
  Batch independent reads; inspect status before editing. Make changes directly
  on `main`; do not create feature branches, PRs or worktrees unless explicitly
  requested. Preserve existing local changes when switching branches.
- Do not commit or push changes. The user reviews, commits and pushes manually.
  Leave completed changes in the working tree and report verification results.
- Use the **GitHub plugin** for remote repository, branch, PR and CI operations;
  use local Git for checkout/diff/status. Do not commit or push on the user's behalf.
- Use the **Railway plugin** for infrastructure/deployment. Known resource IDs are
  in [operations](docs/operations.md#railway-resources); inspect those exact resources
  only when live state matters. Browser console is a fallback for missing operations.
- Reuse dated evidence in [status](docs/status.md); recheck mutable facts before
  acting on them. Read detailed docs only when relevant to the task.
- Bulk JSON/JSONL must be parsed with code. Keep full reports in ignored `artifacts/`
  or existing SourceRecord/ReviewItem tables; print counts, keys and hashes, not
  entire payloads, source archives, HTML, lyrics, or JSON-LD. Bound logs and diffs.

## Verified commands (repository root)

Use Python 3.14 in `.venv` and separate loopback PostgreSQL (Compose port 55432).
Setup/environment instructions: [operations](docs/operations.md#local-development).
The Makefile explicitly uses local/test settings and does not load `.env`.

```sh
make check                       # Django + migration drift checks
make test                        # full discovered PostgreSQL suite
make test-import BUNDLE=/absolute/export/path  # require bundle; no silent skip
make verify                      # check + test + generated-schema consistency
make run                         # local server at 127.0.0.1:8000
make seed                        # existing idempotent artist/reference seeds, local DB
make schema                      # regenerate dictionary and ER views
make import-dry BUNDLE=/absolute/export/path
make import-apply BUNDLE=/absolute/export/path # explicit local write, after clean dry run
```

`PYTHON=/absolute/python` overrides `.venv/bin/python`. `make help` lists targets.
Import summaries go to stdout; full JSON reports go to `artifacts/`.
For production, use the explicit [deployment/import runbook](docs/operations.md),
never these local Make targets. If sandbox blocks loopback access, request the
appropriate tool permission; do not replace PostgreSQL, skip tests, or weaken guards.

## Invariants

- Preserve production database, credentials, volume, artist UUIDs, memberships and
  editorial edits. No resets/flush/fake migrations or database renaming. Additive
  migrations, backups and controlled pre-deploy migration execution are required.
- Organization ownership and artist memberships are distinct. Credits, shared
  identifiers and model permissions do not grant artist access. Scope querysets
  before UUID lookups; enforce same-organization relationships and validated writes.
  Staff are trusted global operators; artist accounts remain non-staff.
- Import by stable source keys/persisted UUID mappings; never title or ISRC alone.
  Preserve original fields, empties, order and assertions. Keep Gone and Chasing
  Ghosts separate. Verify manifest, atomic rollback, conflicts and repeat idempotency.
- Never print/read back secret values into agent context or commit credentials,
  connection URLs, tokens or raw private exports. `.env` is ignored; example values
  are local-only. Production secrets belong in Railway; legal names/contacts and
  source evidence must stay out of public APIs. Never pass admin passwords in commands.
- Do not change websites, DNS/email, public routing, or provision infrastructure
  unless the task authorizes it. Existing outbox schema is not a delivery worker.

## Read on demand

- [README](README.md): entry point and documentation map.
- [Architecture and access](docs/database-architecture.md): decisions and implemented boundaries.
- [Schema dictionary](docs/schema-dictionary.md) / [ER views](docs/entity-relationships.md):
  generated; search the relevant model section, not the whole file. Edit generator/models.
- [Import/integration contract](docs/dezolent-import-plan.md): preservation and future boundaries.
- [Operations](docs/operations.md): setup, environment, verification, deployment/import/bootstrap.
- [Status](docs/status.md): dated evidence, limitations and next milestone.
- `docs/design.md`, `docs/implementation-plan.md`, `docs/schema-plan.md` are historical;
  do not use them as current scope or rediscovery checklists.
