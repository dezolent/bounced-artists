# Backend foundation

Bounced Artists is operated by Bounced Studio / Dezolent, LLC. This foundation
provides staff administration and a private versioned artist API, backed by the
existing Railway PostgreSQL service. Existing websites and infrastructure remain
outside this change. Django 5.2 LTS is the supported baseline.

`config` owns environment configuration and routes; `core` owns the custom user
model and health probes; `artists` owns artist records, memberships, aliases,
contacts, platform references, profile links, and idempotent MVP seeding.
Artist IDs are UUIDs; slugs remain unique human-readable identifiers.

All API access requires an active authenticated user. Non-staff querysets are
filtered by membership before object lookup. All three membership roles may read
their artists. API writes are deferred; staff use Django Admin with explicit model
permissions. Staff are trusted global operators, never artist portal accounts.
Only superusers administer users and memberships, preventing staff privilege
escalation. Roles do not themselves grant Django Admin access.

Production requires an explicit environment, strong secret, PostgreSQL URL,
allowed hosts, and HTTPS CSRF origins. Only loopback PostgreSQL is allowed in local
development. Docker collects static assets without production secrets, Railway's
pre-deploy stage runs checks/migrations/seeding, and Gunicorn only serves WSGI.
Readiness checks database connectivity; liveness does not. Anonymous probes return
only status and do not expose database errors.

Reuse the existing project, database service, attached volume, and web service in
us-west2. Preserve credentials and existing data. Reuse the Railway hostnames and
keep artists.bounced.studio as the intended domain without changing DNS.

Future boundaries: catalog (releases/recordings), integrations (external adapters),
rights (ownership/claims), events, analytics, and publishing (explicit approved
public projections and Netlify hooks). None is implemented in this milestone.
