# Backend Foundation Implementation Plan

**Goal:** Deploy a tested Django administration and artist API to existing Railway resources.

**Architecture:** `config` settings/routes, `core` custom user/probes, `artists` domain/admin/API.
**Tech stack:** Django 5.2 LTS, DRF, PostgreSQL, Gunicorn, WhiteNoise, Docker.
**Spec:** `docs/design.md` and the user's implementation brief.
**Execution:** Inline, as authorized by the user's request to proceed through deployment.

## Constraints

Preserve PostgreSQL data/credentials/volume and its actual database name. No DNS,
email, Netlify, artist website, or out-of-scope infrastructure changes. Never
commit or print secrets. Use a feature branch and PR; preserve public repository visibility.

## Review focus

- Membership filtering must precede UUID retrieval, including model-permission users.
- Staff without superuser status must not grant themselves user or membership access.
- Production must reject missing/unsafe configuration; local setup must not reach production.
- Probes must fail cleanly without leaking database details or bypassing HTTPS for other routes.
- Seeding must preserve edits and migrations must preserve unrelated existing tables.

## Tasks

- [x] 1. Configure isolated dependencies, settings, custom user, Docker and local database workflow.
  Verify missing/unsafe production configuration and local connection guard with subprocess tests.
- [x] 2. Write model/seed tests, observe missing behavior, implement focused models/admin/migrations.
  Verify uniqueness, timezone/URL validation, idempotence and preservation of edited records.
- [x] 3. Write API/admin/probe tests, observe failures, implement membership-scoped read API.
  Verify roles, other-artist UUID lookups, anonymous/inactive users, CSRF and staff restrictions.
- [x] 4. Run PostgreSQL suite, migration checks, production checks and static collection.
  Add CI with separate PostgreSQL. Review the whole branch and resolve material findings.
- [x] 5. Commit/push PR. Configure existing Railway web service, secret and database reference;
  deploy the branch with controlled pre-deploy migrations and seed. Verify logs and HTTPS.
- [x] 6. Inspect backups, enable an available appropriate schedule within authorization,
  document any access/cost limitation, and provide secure interactive admin bootstrap instructions.

## Verification record before deployment

Local PostgreSQL suite passed (31 tests); migrations and repeated seeding passed.
Production-settings Gunicorn smoke test passed for liveness, database readiness,
anonymous API denial, admin login page, hashed CSS, secure CSRF cookie and HSTS.
Deployment checks passed with only the two documented HSTS policy warnings silenced.
Independent read-only review found two configuration issues: blank local secret
handling and libpq destination overrides. Both were reproduced and fixed with
regression tests. Seed identity now also survives slug edits via internal provenance.
Docker execution passed in both Railway and GitHub CI; Docker is absent locally.

Railway rejected a legacy Config as Code path because that mechanism is deprecated.
Lifecycle settings were applied through the supported service configuration API,
and the exact non-secret update payload is preserved in `deploy/railway-web-service.json`.
The legacy repository configuration file was removed before deployment.

## Deployment verification

Railway deployment `b890f639-a62d-45f7-a564-1231c15062dd` succeeded on 2026-10-20
from commit `856291f3d2200ecdc24810e7bb620f74c077e6bf`. Runtime logs confirmed
all committed migrations applied and `MVP artists: 3 created, 0 preserved`.
GitHub Actions run `39382989779` passed, including PostgreSQL 18 tests and Docker.
Both existing Railway hostnames returned 200 for health/readiness and the admin
login page, 200 for hashed admin CSS, and 403 for anonymous artist API access.
HTTPS responses included HSTS and the login page set a Secure CSRF cookie.

The existing service was renamed to `bounced-artists-web`. Database, volume,
credentials, region and private networking were preserved. The operator created
the administrator interactively; authenticated Admin access and all three managed
artist records were verified in production. DNS, email, Netlify and artist websites
were untouched.

After the operator upgraded to Pro, daily PostgreSQL volume backups were enabled
on 2026-10-20 (every 24 hours, six-day retention). The initial on-demand backup
completed successfully at 103 MB. Point-in-time recovery remains off. No restore
drill has been performed; recovery must be tested in an isolated destination.
