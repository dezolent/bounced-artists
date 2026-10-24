# Deploy the self-hosted daily scrapers

Implemented locally **2026-10-24** for Spotify, Apple Music, YouTube, SoundCloud,
TikTok, Instagram, Shazam and Musixmatch. **Deezer and Boomplay are excluded.**
This replaces the Omkar-hosted requests when the new cron image is selected.
The web service, PostgreSQL and existing memberships/catalog remain in place.
No additional Railway service, public endpoint, API subscription or database
migration is required. Code is left for the user's manual review/commit/push.

## What is implemented

The cron image bundles the eight MIT-licensed implementations at explicit Git
revisions in [sources.json](../deploy/scrapers/sources.json), retaining licenses.
Django runs in Python 3.14. Providers run in separate Python 3.12 processes with
isolated dependencies, working directories and fixed loopback-only ports. The
supervisor starts them, checks their pinned revisions/import readiness, runs the
existing bounded daily sync, then cleans up process groups and returns its status.
Provider processes receive only their allowlisted settings, not database/Django,
R2, Omkar or official Spotify application credentials. Upstream logs are suppressed;
the application emits safe structured progress and error codes instead.

In `self_hosted` mode, the client omits the Omkar key and has no hosted fallback.
Each run records `scraper_backend`, a hashed `backend_identity`, and the pinned
`scraper_revision`. Profile/storefront/backend/revision fingerprints prevent
same-day retries from reusing another backend's evidence. `collector=omkar`
identifies the software implementation; the run records where it executed.

The existing six collectors retain catalog discovery/enrichment, snapshots,
metrics, identity checks, grants, request/time budgets and editorial preservation.
Shazam/Musixmatch are **catalog enrichments**, not unverified profile searches:

- Shazam follows accepted/granted Apple song IDs attributed to the current verified
  customer Apple profile. It collects song metadata, audio analysis and popular
  segments. Segment counts are not lifetime total Shazams or Apple plays.
- Musixmatch follows accepted/granted Spotify IDs attributed to the current verified
  customer Spotify profile, then confirms the returned native track identity. It
  collects details, lyrics, writers and credits. Full, explicitly unrestricted
  lyrics can create one private **lyrics candidate** in the song's Lyrics & files.
  Candidates reference immutable source text in PostgreSQL, require explicit grants,
  and need no cron R2 credentials. They never overwrite existing/removed lyric
  choices, edited text or synced lyric files, and are not approved or published.
  Pausing a collection job does not hide an already imported, still-authorized
  candidate; removing profile verification, grants or accepted matches does.

Only Dezolent, Morbeato and SRY4U remain targets. No collaborator becomes a target.
Names/ISRCs are not joins. Enrichment jobs revalidate source attribution, accepted
matches and active grants at collection/import/read time. Jobs with no valid seeds
make no platform requests; progress reports `catalog_seed_count`. Configuration
adds jobs after source catalog matches exist and preserves operator pauses/status.

Base collection runs before enrichment. A completed seed attempt saves a checkpoint;
next invocation rotates after that seed. Normal per-track failures advance the
checkpoint after their evidence is consumed, while fatal budgets/cancellation leave
that seed for replay. The checkpoint is profile/backend/revision scoped and prevents
large enrichment catalogs repeatedly restarting at the first songs. This does not
claim exhaustive daily coverage or resumable pagination for the original six providers.

## Your Railway steps

1. **Review staged and unstaged changes, stage the final versions, then commit and push to `main`.** Allow the web service to deploy
   this code too, so the new source-backed lyric previews and Insights are available.
   Leave its existing Dockerfile/start command, R2 settings and routing unchanged.

2. Open the existing **`bounced-artists-daily-sync`** service in production. In its
   build settings, set **Dockerfile Path** to:

   ```text
   deploy/scrapers/Dockerfile
   ```

   Keep repository root as the build context/root directory. Do not point Root
   Directory at `deploy/scrapers`: the image also needs the Django project.

3. In that cron service's deployment settings, set **Start Command** to:

   ```text
   python scripts/self_hosted_sync.py
   ```

   Keep daily cron **`0 9 * * *`** (09:00 UTC), restart policy **Never**, one replica,
   no web healthcheck/public domain, and region **`us-west2`**, alongside PostgreSQL.
   Apply settings directly in Railway. [railway-artist-sync.toml](../deploy/railway-artist-sync.toml)
   is a reference, not a new Config File Path; Railway's legacy Config as Code is
   deprecated and this project previously rejected selecting that path.

4. Keep the existing `DATABASE_URL`, `DJANGO_SETTINGS_MODULE`, Django settings and
   `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` references. The latter are still used
   by the official Spotify track API. The Omkar key may remain configured but will
   not be used in self-hosted mode; do not delete shared variables needed elsewhere.
   Set/review these cron limits:

   | Variable | Suggested initial value | Meaning |
   | --- | --- | --- |
   | `ARTIST_SYNC_REQUEST_LIMIT` | `24000` | Safety allowance per invocation, not a monthly API quota |
   | `ARTIST_SYNC_JOB_REQUEST_LIMIT` | `1000` | Per-profile allowance |
   | `ARTIST_SYNC_TIME_LIMIT` | `7200` | Two-hour cooperative invocation bound |
   | `ARTIST_SYNC_JOB_TIME_LIMIT` | `300` | Five-minute cooperative per-job bound |
   | `ARTIST_SYNC_JOB_LIMIT` | `250` | Maximum due jobs |
   | `ARTIST_SYNC_CATALOG_ENRICHMENT` | `1` | Enable catalog-seeded Shazam/Musixmatch jobs |

   Existing explicit values take priority over supervisor defaults, so an old
   `18000` allowance will stay `18000` unless you change it. In-flight requests or
   atomic database operations may finish after deadlines. Large initial catalogs
   can need multiple runs; evaluate real timing/coverage before increasing limits.

   **Do not add eight URL variables manually.** The supervisor forces
   `ARTIST_SCRAPER_MODE=self_hosted` and sets all eight loopback URLs and source
   revisions for its sync subprocess. No new Omkar key, Apple key, proxy or R2
   setting is mandatory for this setup. Startup success is not proof that every
   platform will accept Railway's egress IP.

5. Deploy the updated cron image, then run the cron **once manually**. Do not start
   overlapping manual runs. Expect:

   - `Self-hosted scraper runtime ready (8 private providers).`
   - Backend preflight JSON with eight `ready` entries.
   - Job progress with `"scraper_backend": "self_hosted"`, source revisions,
     snapshot counts, elapsed/persistence time and fixed endpoint errors.
   - A final summary, process cleanup and container exit. Real partial failures
     remain failed/incomplete; they are not disguised as full success.

   In the app, verify Overview/Insights for the three customers, song metadata and
   metrics, and Musixmatch lyric candidates in **Catalog → song → Lyrics & files**.
   Confirm some Shazam/Musixmatch jobs have nonzero `catalog_seed_count` and that
   new data is scoped to the intended recordings. If no accepted attributed catalog
   source exists yet, collect Spotify/Apple first; the next configuration pass can
   add the dependent jobs. Review candidates before editing/using/publishing them.

The latest read-only Railway inspection during implementation still showed the
hosted image settings (`Dockerfile`, `sh scripts/sync_artist_platforms.sh`) in
`us-west2`. The latest deployment reported CRASHED. This work did not alter those
live settings, start a scrape, deploy or migrate production.

## Local verification without scraping

From the repository root:

```sh
docker build -f deploy/scrapers/Dockerfile -t bounced-scrapers:local .
docker run --rm --network none bounced-scrapers:local \
  python scripts/self_hosted_sync.py --health-check-only
```

This starts/imports all eight pinned source servers, checks revision/readiness,
cleans up and exits. It does not load Django production secrets, use PostgreSQL or
contact the target platforms. It is also a safe temporary Railway Start Command
for verifying an image before changing the command back to the normal supervisor.
A health-only scheduled job collects nothing, so restore the normal command.

`python manage.py check_scraper_backends` validates current configuration without
network calls. Its `--probe` option checks self-hosted `/health` only; it never
probes the hosted Omkar service or artist endpoints. The supervisor supplies the
loopback variables only to its sync subprocess, so invoking that command in a
separate shell requires the same non-secret mode/URL configuration. Configuration
errors never print URL credentials/secret values.

## If a provider fails

Missing imports/revisions/ports stop the batch before collection. A provider crash
stops the batch and cleans up all children. Platform 4xx/5xx/blocking still produce
private failure receipts while independent work continues within budget. View the
structured endpoint/error counts rather than treating every checkpoint as a new
error. The runtime will not silently fall back to the hosted quota.

The sources support optional proxy settings. Only configure one if actual provider
errors show it is needed, and place its credential-bearing URL directly in Railway
service variables, never a command, commit or chat. Current allowlists include
`SPOTIFY_PROXY`, `APPLE_MUSIC_PROXY`, `YOUTUBE_PROXY`, `SOUNDCLOUD_PROXY`,
`TIKTOK_PROXY`, `INSTAGRAM_PROXY`, `SHAZAM_PROXY`/`SHAZAM_FALLBACK_PROXY` and
`MUSIXMATCH_PROXY`. [Supervisor allowlists](../scripts/self_hosted_sync.py) are the
exact supported set, including narrowly scoped optional provider settings.

Source endpoints may make multiple requests internally; our HTTP attempt counts
measure local API calls plus official Spotify API calls, not every upstream fetch.
Self-hosting removes the Omkar hosted allowance, not platform limits, compute,
network or proxy costs. Apple artist play analytics still need an authorized
provider feed or a separate Apple Music for Artists export/import workflow.

## Rollback

Restore only the cron's Dockerfile Path to `Dockerfile` and Start Command to
`sh scripts/sync_artist_platforms.sh`, keep its Omkar key reference available,
and set `ARTIST_SCRAPER_MODE=hosted` / `ARTIST_SYNC_CATALOG_ENRICHMENT=0` explicitly
if switching modes outside the supervisor. Redeploy. Operator-disabled enrichment
jobs remain disabled; configuration does not silently unpause them later.
Existing source snapshots, metrics, lyric candidates, grants and catalog edits
remain stored. Do not reset PostgreSQL, delete assets or rerun migrations as a
rollback mechanism. Existing lyric candidate previews remain source-backed while
current profile/connection/grant/match authorization holds.


## Verification evidence

- **545 PostgreSQL tests passed**, including existing private import/access tests,
  backend/credential separation, fingerprint isolation, source joins, lyric candidate
  authorization/preservation and interrupted-run checkpoint recovery.
- Django checks, migration-history/drift checks, generated schema consistency and
  whitespace checks passed. Domain models remain at 80; no migration was added.
- The final Docker image built successfully. With `--network none`, all eight
  pinned source processes started and the actual Django backend client reported
  eight ready health probes. No production secrets or database were supplied.
- Independent review's pause/read and interrupted-checkpoint findings were fixed
  and covered. Tests/build/probe receipts are ignored under
  `artifacts/selfhost-implementation/`.

This verifies local code, packaging and readiness. It does **not** verify a live
scrape from Railway egress; run the manual production verification after your
review, push and cron settings update. No code was committed/pushed by the agent,
and no Railway setting, deployment, production scrape or database schema changed.
