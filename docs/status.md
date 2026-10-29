# Current status and evidence

Last verified locally **2026-10-29**; production logs rechecked **2026-10-29**. Evidence below retains its own date. This is a dated record, not a live monitor. Recheck
mutable facts only when the next task depends on them; use known IDs in
[operations](operations.md#railway-resources) instead of rediscovering resources.

## Daily sync investigation (2026-10-29)

- Railway deployment `4988bfbb` ended crashed with 12 succeeded and
  12 failed jobs. Safe job codes identified missing RapidAPI audio credentials,
  Instagram login requirements, provider errors, and YouTube/TikTok job time
  limits. The two hyphenated RapidAPI variable names exist on the service; their
  contents were not inspected. The shell boundary can discard these names.
- A later deployment `515f058d` reported SUCCESS, but its available runtime
  logs did not establish that a scheduled collection completed. Local changes
  isolate social startup failures from streaming, attach YouTube videos only
  when an accepted streaming link and matching title identify one song, and
  stop repeated calls to rate-limited social endpoints within a job. These
  latest changes have not been deployed or verified against live providers.

## Social review cleanup and RapidAPI work (2026-10-27)

- Following the owner's instruction to match Morbeato and SRY4U recordings by
  matching title and very close duration despite differing release dates, a
  fresh verified PostgreSQL backup, hashed plan, rollback rehearsal and atomic
  production transaction archived 25 Morbeato and three SRY4U duplicates and
  moved 88 relationships. Readback verified all 88 moves, 28 active canonical
  recordings, both existing Gone cards still active, unchanged related table
  counts and idempotent reapplication. No cover assets were archived. Blissful
  Chaos remains separate because its durations differ by 15.675 seconds. The
  two existing Gone cards do not yet correspond cleanly to separate original and
  2024 releases: one card contains links to both releases and the other has only
  a SoundCloud link and no ISRC. Release-specific mapping needs further review.
  The private backup and approved plan are in ignored
  `artifacts/catalog-reconciliation-2026-10-27/`.
- A later production preview revealed that an older SoundCloud-only card was
  hiding directly corroborated pairs. After a fresh verified logical backup,
  hashed preview and rollback rehearsal, a guarded transaction archived 26
  Morbeato duplicate recordings and moved 78 links, retaining 78 collisions.
  Readback found 164 active recordings, unchanged related table counts and an
  idempotent repeat. At that point 30 SoundCloud candidate pairs remained: 26
  Morbeato, three SRY4U and one Dezolent; one had a duration mismatch, and saved SoundCloud
  records do not contain ISRCs or direct Spotify track cross-references. The
  private candidate report is in ignored `artifacts/catalog-reconciliation-2026-10-27/`.
  The canonical-selection fix remains local; `make verify` passed 609 tests.
- Production had 5,560 open TikTok video and 1,438 Instagram post discovery
  notices with only an artist candidate and no field observations. After a
  hash-guarded preview, local before-image backup, and rolled-back rehearsal,
  dismissed those 6,998 notices in a single audited transaction. Readback found
  727 open items and confirmed all affected source records and candidates remain.
  Railway declined a fresh volume snapshot because its ten-backup limit was
  reached; an earlier volume snapshot remained available. The previous cron can
  create more notices until the local prevention change is deployed.
- A verified PostgreSQL logical backup was saved locally before a second guarded
  recording reconciliation. A rollback rehearsal and atomic apply archived six
  further duplicates, filled six previously blank unedited language fields, and
  moved 43 relationships while retaining 21 collisions. Readback showed 190 active
  recordings, unchanged related table counts, and an idempotent repeat. The 56
  uncertain pairs still need evidence. Identifier-only merging would conflict
  with the preserved Gone / Chasing Ghosts exception. Destination data cleanup
  and primary TikTok sound selection remain unfinished.
- Local, undeployed work changes audio feature collection to Musicae's batch API
  with five IDs per request, a persistent rolling 24-hour limit of ten attempts,
  and Dezolent priority. No live RapidAPI collection has been run or verified.
- After these local changes, `make verify` passed 607 PostgreSQL tests, migration
  checks and generated-schema consistency checks.

## Catalog consolidation and audio features (2026-10-27)

- Local changes replace active Shazam collection with Spotify audio-feature
  enrichment, limit Musixmatch to missing lyrics, consolidate the song display,
  and add song and artist audio profiles. Spotify has restricted access to its
  audio-features endpoints; production credentials and entitlement have not been
  validated for this integration. Changes remain uncommitted and undeployed.
- Created an on-demand Railway volume backup before reconciling production.
  A guarded, hashed dry run and rollback rehearsal preceded a transaction that
  archived 20 corroborated duplicate recordings and moved 73 relationships;
  originals and 60 colliding relationships were retained. Readback verified
  all 133 planned relationship actions, 196 active recordings, unchanged source,
  observation, asset, grant, membership, match and review counts, and repeat
  application safety. No cover assets were archived.
- The broader preview identified 56 additional uncertain candidate pairs and
  six language conflicts (subsequently handled as recorded above). The uncertain
  pairs require further evidence or editorial review;
  duplicate covers with differing provider URLs also remain. The previous cron
  deployment remains in service until the local changes are reviewed and deployed.
- `make verify` passed 596 PostgreSQL tests and schema checks; the offline
  seven-provider scraper health check passed. Private plan and readback evidence
  is retained in ignored `artifacts/catalog-reconciliation-2026-10-27/`.

## Production volume expansion and daily sync diagnosis (2026-10-27)

- Railway reported the existing PostgreSQL volume `b847167f` at **500 MB**,
  with **0.484 GB** used; the dashboard displayed **97%** usage. Seven-day
  disk metrics ranged from 0.120 GB to 0.484 GB. A daily volume backup from
  approximately two hours before inspection was available to restore (407 MB);
  PITR remained off. No table/filesystem size breakdown was collected, so the
  precise storage contributors remain unverified.
- With explicit user authorization and the user's passkey verification, live
  resized that same volume to **5,000 MB (5 GB)**. Dashboard and Railway plugin
  read-back both confirmed the new capacity, existing mount and `us-west2` region.
  PostgreSQL retained deployment `af1f8ab4` and remained online; the web service
  remained online. At the last measured usage this is approximately **10% full**.
  No data was deleted, credentials changed, database recreated or deployment started.
- Today's cron deployment `b6d47db9` uses commit `e3134ab`, the intended pinned
  self-hosted image/supervisor, one replica in `us-west2`, daily `0 9 * * *`,
  restart `NEVER`, with no staged changes. It reached its final summary at
  **09:30:31 UTC (05:30 Eastern)**: **5,589 snapshots, 1,896 requests, 14 succeeded,
  11 failed, 0 skipped**, no overlap lock. The final `CommandError` deliberately
  returns nonzero for incomplete collection; the scheduler's CRASHED state does
  not mean nothing was collected. No disk-full error appeared in the inspected
  cron or filtered PostgreSQL logs.
- All three Spotify, Apple Music and SoundCloud jobs succeeded. Two YouTube jobs
  succeeded; one exceeded the five-minute job limit (305.9 seconds). One TikTok
  job succeeded; another exceeded the limit (300.2 seconds), while a third had
  music HTTP 404/upstream errors. All three Instagram jobs reported
  `users-posts: provider_login_required`. Two Shazam jobs reported blocked audio
  analysis/popular segments, while two succeeded (one had no eligible seeds).
  All three Musixmatch jobs failed with blocked track details; one also rejected
  six detail identities and one credits identity. Those safeguards remain intact.
- **The cron is not fixed and a fully successful production run is not verified.**
  Enlarging storage does not remove provider login/blocking or per-job time limits.
  Provider access needs a supported, validated configuration; current evidence
  does not prove a proxy/token would solve it. The original provider pagination
  still has no cross-invocation checkpoint, so large profiles can hit the same
  limit on later days. No providers were disabled, time budgets raised, failure
  exits hidden, new scrape started or production code deployed during this task.
- Parsed job-level evidence and resize screenshot are ignored under
  `artifacts/railway-health-2026-10-27/`. This was live infrastructure/log diagnosis;
  no application code changed or application test suite was run.

## Daily sync failure investigation (2026-10-24, run `a108dbd1`)

- Parsed the attached run log: the supervisor started all eight providers and the
  batch reached its final summary after about 36 minutes. **4,513 snapshots,
  1,596 HTTP attempts, 2 succeeded, 11 failed**. The final `CommandError` is the
  deliberate nonzero exit for incomplete collection, rather than an unhandled
  runtime crash. Eight jobs reported `provider_collection_truncated`; three hit
  `job_time_limit` (one TikTok, two Musixmatch).
- Instagram's three jobs failed at `/users/posts` with HTTP 500. A local call
  through the exact pinned source reproduced `InstagramGated` after the mobile
  and web post surfaces refused access. The standalone route concealed that
  exception as HTTP 500, triggering repeated outer retries. TikTok also reported
  music 400/404/502; Shazam audio/segment and Musixmatch detail/credit requests
  reported 500. Catalog identity mismatches were correctly rejected. Those
  original generic 500s cannot establish their exact upstream cause; one customer
  track's Shazam/Musixmatch requests succeeded from local Docker egress.
- Added private adapters preserving fixed blocked/login/upstream/internal error
  codes while suppressing exception text. The self-hosted client stops repeating
  a blocked/login-gated endpoint for that job; independent surfaces continue.
  Shared Shazam resolve/page lookups and Musixmatch native-ID lookups now reuse
  bounded, five-minute in-memory results, reducing repeated upstream requests.
  Exceptions are not cached as successful data. Source snapshots, identity checks,
  budgets, incomplete receipts and nonzero failure exits remain enforced.
- Railway read-back confirmed the intended self-hosted Dockerfile/supervisor,
  one replica in `us-west2`, daily `0 9 * * *`, restart `NEVER`, and no staged
  changes. The supplied deployment has since been replaced by `3d830ddb` from
  commit `425b527`; that deployment's SUCCESS state is not evidence of a completed
  scrape. No production variables, settings, deployment or database were changed
  during this investigation. Changes remain uncommitted/unpushed on `main`.
- **553 tests passed** in `make verify`, including clean PostgreSQL teardown,
  Django/migration and schema checks. The final focused run passed 29 tests,
  including one additional empty-error-body regression. The updated Docker image
  built, all eight providers passed offline startup/cleanup, and offline fixture
  checks against the actual four affected pinned route modules passed. Independent
  review found no actionable defects. Receipts are under ignored
  `artifacts/crash-a108dbd1/`. A successful production rerun is **not verified**.
  Login/IP restrictions may still require provider-specific egress configuration;
  raising budgets or hiding partial failures would not remove those restrictions.

## Eight-source self-hosted collection implementation (2026-10-24 working tree)

- Bundled Spotify, Apple Music, YouTube, SoundCloud, TikTok, Instagram, Shazam and
  Musixmatch in a pinned, isolated cron image. Deezer/Boomplay excluded. Provider
  processes bind loopback only, receive narrowly allowlisted settings, and never
  receive database/Django/R2/Omkar or official Spotify application credentials.
  Supervisor readiness, exit status, signals, cleanup and private build-context
  exclusions are covered; hosted mode remains the existing default outside it.
- Added explicit self-hosted backend routing with no hosted fallback/key forwarding,
  backend/revision-scoped snapshot retry isolation and safe config/readiness checks.
  Structured run receipts record mode, revision, timing and candidate/seed counts.
- Catalog-seeded Shazam/Musixmatch jobs require verified customer Apple/Spotify
  profiles, attributed accepted IDs and current grants/decisions. Source identities
  are revalidated without rebuilding the whole catalog per row. Pauses preserved.
  No names/ISRC joins or collaborator targets. Base collections run first; seed
  checkpoints rotate work and survive interruptions/time limits.
- Shazam metadata/audio/segment observations and Musixmatch details/credits are in
  Insights/catalog metadata. Explicit unrestricted full lyrics can create private,
  source-backed candidates without R2/files, preserving edited/removed lyrics.
  Source/asset backlinks, text hashes, current grants/native matches and profile
  authorization protect previews; pausing scheduling alone does not hide them.
- **545 PostgreSQL tests passed**, including private imports and access regressions.
  Django/migration, generated schema and whitespace checks passed; final Docker
  build plus eight actual Django backend health probes passed with network disabled.
  Independent review findings were fixed and covered. Evidence is ignored under
  `artifacts/selfhost-implementation/`.
- [Railway walkthrough](self-hosted-scrapers.md) gives the exact existing-cron
  Dockerfile/start command and limits. Latest read-only inspection still used the
  hosted Dockerfile/entrypoint in `us-west2` and reported CRASHED. No live settings,
  deployment, scrape or migration were changed. Work remains on `main` for the
  user's staging/review/commit/push; production provider success needs a manual run.

## Omkar source/self-hosting assessment (2026-10-24)

- Inspected all ten supplied GitHub repositories with the GitHub plugin. Nine
  contain MIT-licensed scraper/server implementations and Dockerfiles. Deezer's
  linked tree contains documentation/image only; Boomplay's stray Deezer parser
  does not supply a complete Deezer service.
- Current six-platform endpoint paths exist in the source servers. Direct source
  execution can remove Omkar's hosted quota, but runtime compatibility, target
  availability and costs still need live validation. No source scraper ran.
- Shazam song details has no total-Shazams field; popular-segment counts are a
  different measure. Musixmatch exposes lyric restrictions/copyright and must not
  overwrite editorial lyrics. Boomplay has public counters and a browser runtime.
- [Assessment, pinned revisions and cost boundaries](omkar-source-assessment.md)
  distinguish findings from recommendations. Full inventories/route snapshots are
  ignored under `artifacts/omkar-source-assessment/`. Nine route files parsed as
  Python AST. Only documentation/evidence changed; no application edits, deployment,
  provider switch, proxy purchase or infrastructure provisioning occurred.

## Catalog metadata enrichment and Apple analytics research (2026-10-24 working tree)

- Fixed title-only song discovery: fill missing date/precision, language, duration,
  explicit flag, source-linked unverified ISRC and genres; add private provider-CDN
  cover URL references with explicit grants. Preserve existing metadata, staff/
  artist manual clears, overrides, cover choices, removals and access decisions.
- Song pages show source-labelled album/label/copyright/composer/credits/audio
  facts with full source links. Cover references do not download files, approve
  rights or publish data. A new no-network `backfill_platform_catalog` command has
  rollback-only preview, explicit apply, bounded receipt selection and the same
  advisory lock/verified customer guards as collection.
- [Apple Music research](apple-music-analytics.md) distinguishes public catalog/
  MusicKit, Songstats charts/playlists, Viberate first-party data, Apple's separate
  provider Music Analytics API and artist CSV exports. User confirmed Artist-only
  account access. No private-analytics connection or CSV importer was implemented.
- **493 PostgreSQL tests passed** with private imports and access regressions.
  Independent review's Operations/admin manual-clear finding was fixed and covered.
  Django/migration, generated schema, whitespace and browser checks passed; synthetic
  local preview writes were rolled back and verified. Evidence is ignored under
  `artifacts/apple-music-research/`. Changes remain uncommitted/unpushed on `main`;
  no production backfill, scrape, Apple connection or deployment was performed.

## Long-running cron diagnosis and guards (2026-10-24)

- Parsed the user-provided 1,002-row cron CSV. Most rows are progress events sent to
  stderr and classified as errors, repeating cumulative failure counts. The visible
  receipts show five exhausted endpoint failures across three profile runs, with
  successful snapshots continuing to accumulate. The latest live log before the
  user stopped the container showed 2,262 snapshots for the current TikTok profile.
- Railway confirmed the cron in `us-east4-eqdc4a` and PostgreSQL/its volume in
  `us-west2`; cross-region database round trips likely contribute to persistence
  latency. CPU/memory were low. With explicit user approval, moved only the cron
  configuration to one replica in `us-west2` and read back the unchanged daily
  `0 9 * * *` schedule. No database, volume or web-service changes were made.
- Local code now emits explicit info/warning/error progress levels on stdout,
  keeping the summary as the final JSON line. Configurable cooperative limits
  default to five minutes per profile and two hours per invocation. Timed-out
  profiles retain committed data and are incomplete; later profiles continue.
  In-flight requests/atomic operations can finish after a deadline. Large profiles
  can still be incomplete; pagination is not yet resumable across invocations.
  Elapsed/persistence seconds are included in private receipts and progress logs.
- **484 PostgreSQL tests passed**, including private import regressions; 72 focused
  collector tests also passed. Django, migration-history/drift, schema and whitespace
  checks passed. Evidence is ignored under `artifacts/cron-runtime-2026-10-24/`.
  Code remains uncommitted/unpushed on `main`. No new scrape was started and the
  runtime/logging patch has not been deployed. Production timing improvement and
  upstream Omkar availability remain unverified after the region change.

## Collection coverage and automatic song discovery (2026-10-23 working tree)

- Railway scheduled-run receipts show an Instagram job consumed 647 attempts and
  exhausted the shared 1,000-attempt batch allowance during Spotify. Later Spotify
  and Instagram jobs made zero requests. The inspected Apple job succeeded with
  16 requests/snapshots; public Apple metadata does not include audience play counts.
- Added per-profile request budgets, a larger total allowance, one delayed same-page
  retry, direct Instagram list statistics without redundant complete-item detail
  calls, and independent Apple top-song/profile discovery. Same-day post details
  can be reused safely after failures. Real errors remain incomplete.
- Capped non-paginated Spotify shelves record provider limits rather than failed
  requests. Overview/Insights distinguish **Provider limited**, including actual
  returned/reported coverage, from a failed **Incomplete** collection.
- Attributed verified customer tracks create private draft catalog songs and explicit
  grants/matches with source evidence. Existing accepted IDs/URLs are reused and
  same-day receipts can backfill missing songs. Edits, archived/removed data,
  rejected/pending matches and completed review decisions remain intact. Titles/
  ISRCs never merge recordings; unrelated posts/videos never become songs.
- **419 PostgreSQL tests passed**, including private import regressions. Django,
  migration drift, schema and static checks passed; independent review found no
  blocking findings. Diagnostic summaries, tests and rolled-back synthetic previews
  are in ignored `artifacts/sync-diagnostics/`.
- Saved cron variables `ARTIST_SYNC_REQUEST_LIMIT=15000` and
  `ARTIST_SYNC_JOB_REQUEST_LIMIT=1000` using the Railway plugin with deploys skipped.
  They apply on the next deployment. Code remains uncommitted/unpushed on `main`;
  no live scrape, code deployment, migration or production catalog write occurred.
  Production success/runtime still needs a new run after the user's manual deploy.

## Earlier sync resilience, catalog metrics and overview dashboard (2026-10-23)

- Independent endpoint/item failures preserve remaining coverage and incomplete
  receipts. Transient profile failures can continue Spotify/Apple discography and
  SoundCloud public uploads using verified locators. Failed retries reuse eligible
  same-day details only for the same profile/storefront/day; force refresh bypasses
  reuse. Added bounded jitter, progress/error counts, actual timestamps, cached
  lookups and validated atomic bulk metric inserts.
- Catalog song pages show current metrics, trends, observed times and exact source
  links through accepted, active and explicitly granted platform matches. Song-scoped
  Insights and reverse catalog links use platform identity, never titles or ISRCs.
- Overview includes per-artist Platform pulse and a staff-only three-customer
  comparison across five platforms. Current metrics deduplicate aliases, revisions
  and shared objects. Unknown values, incomplete coverage and overlapping audiences
  remain explicit; source/series/artist/organization boundaries are enforced.
- **377 PostgreSQL tests passed**, including the private export bundle. Django,
  migration drift, schema, static and whitespace checks passed. Independent review
  found cross-series revision ordering and Apple storefront cache guards; both
  received regression fixes. Synthetic desktop/mobile previews and navigation checks
  passed; temporary local fixture writes were rolled back. Evidence is under ignored
  `artifacts/platform-dashboard-preview/`.
- Local edits remain uncommitted/unpushed on `main`. No new live scrape, deployment,
  migration or infrastructure change was made for these improvements. Production
  runtime/performance after deployment has not been measured.

## Railway cron configuration correction (2026-10-22 evening Eastern)

- The user pushed collector commit `bf77044` and created the daily sync service.
  Their initial manual execution started a Caddy server from
  `dezolent/bounced-studio`, so its elapsed time did not represent scraping.
- Corrected that service to `dezolent/bounced-artists` on `main`, Dockerfile builder,
  start command `sh scripts/sync_artist_platforms.sh`, daily `0 9 * * *`, no pre-deploy
  command, and restart policy `NEVER`. Read-back confirmed the live settings and
  no staged changes. Railway rejected the deprecated config-file-path setting;
  the settings were applied directly instead. No secret values were read.
- Deployment `9cc43a02-2ce2-4c0b-86bc-051f74a49914` built successfully and is
  scheduled daily at 09:00 UTC. After automatic approval review initially blocked
  Run now, the user explicitly authorized one execution. It started at
  **2026-10-23 01:56:30 UTC**. Startup reported zero verification changes, no missing
  workspaces, **15 eligible profiles** and zero invalid profiles.
- Production artist Insights displays new YouTube audience/video observations and
  daily history for Dezolent; Morbeato's Spotify collection also produced data.
  Dezolent's YouTube receipt saved **49 snapshots over 52 HTTP attempts**, then
  failed with `http_502`. Another inspected receipt failed with `http_502` after
  three attempts and zero snapshots. Morbeato's Spotify run saved **148 snapshots
  over 149 requests**, then reported `provider_collection_truncated`. A real track
  ID from an album's track list has both Omkar and successful official Spotify
  snapshots, confirming album/track separation and working official API enrichment
  for that inspected track.
- The batch finished after about **30 minutes**. Its final summary at
  **2026-10-23 02:26:08 UTC** reported **427 snapshots, 462 requests, 4 succeeded,
  11 failed, 0 skipped**, with no overlap lock. Read all eleven failed receipts:
  **9 `http_502` and 2 `provider_collection_truncated`** (Morbeato/SRY4U Spotify).
  The successful jobs were **Dezolent Apple Music, Morbeato Apple Music, SRY4U
  YouTube and SRY4U SoundCloud**. All fifteen platform statuses were read back in
  the three customer workspaces. Railway correctly reports `cronFailed` after the
  nonzero command exit. Full collection is not verified. Successful snapshots
  survive failed jobs; no additional manual execution was started. The bounded
  ignored report is `artifacts/artist-sync-preview/production-manual-run-report.json`.
  Web/database settings were not changed.

## Daily platform collection and artist Insights (2026-10-22 implementation evidence)

- Implemented daily Omkar collectors for Spotify, SoundCloud, Apple Music, YouTube
  and Instagram, plus official Spotify track enrichment. Spotify albums expand to
  actual track IDs. Pagination, finite request/retry limits, PostgreSQL overlap
  locking, immutable snapshots, same-day metric revisions and per-artist discovery
  review preserve the existing editorial/publication boundaries. Apple compilation
  tracks are checked by artist ID and the verified storefront is preserved.
- Collection is explicitly restricted to verified HTTPS profiles for the Bounced
  Studio customer identities **Dezolent, Morbeato and SRY4U**. Collaborators cannot
  become scrape targets through credits, verification or accidental managed status;
  removed profile links are excluded.
- The owner authorized the three official websites' published links as verified
  sources. Inspected 121 HTML pages and recorded public URLs/page hashes in
  `artists/data/official_workspace_links.json`; the two remaining failed page reads
  were Cloudflare email-protection paths, not music pages. Locally applied an audited
  verification: **11 profiles created, 15 existing profiles verified, 255 recording
  links and 11 release links accepted**. Read-back checked all 266 accepted links
  against the final website identity matcher with **zero mismatches**. Repeat preview
  reported **zero changes**. Canonical metadata, identities, memberships and grants
  were not changed. The **21 collaborator artist records** remain intact.
- Deduplicated profile URL aliases and configured **15 enabled local jobs: five
  platforms for each of the three customers**. The web pre-deploy and cron entrypoint
  apply the offline website assertions with one-time receipts; later operator
  rejection, removal or URL replacement survives reruns. No live Omkar/Spotify API
  requests or production verification writes were made.
- Added private artist **Insights** with platform/search/release filters, metadata,
  artwork/preview/source links, audience values, 30-day daily history/trends and run
  freshness. Accepted existing URL-only catalog destinations resolve to source IDs.
  Artist/viewer source lookups and metrics remain scoped to the selected artist and
  organization. Source payloads are allowlisted for display and never published.
- Added `scripts/sync_artist_platforms.sh`, a separate daily Railway cron config,
  and [activation/runbook details](artist-platform-sync.md). No migrations or new
  dependencies. **332 PostgreSQL tests passed**, including the private export
  bundle. Django/migration checks, generated-schema consistency, static collection,
  shell/TOML validation and whitespace checks passed. Synthetic collection/detail
  pages and expandable daily history were checked in the browser; preview evidence
  is under ignored `artifacts/artist-sync-preview/`.
- **At implementation verification: local only; uncommitted on main. No commit, push, production write, live scraper
  call, cron service creation or deployment occurred.** Railway configuration access
  recovered; the production web service still follows `main`. The user reports
  shared `OMKAR_API_KEY`, `SPOTIFY_CLIENT_ID` and
  `SPOTIFY_CLIENT_SECRET`; their values were not read. Activation still needs the
  user's manual push/deployment and the separate cron-service setup. The next web
  deployment will apply the authorized link verification in production. Do not
  interpret local job configuration as running production collection.

## Implemented and verified

- PRs [#1](https://github.com/dezolent/bounced-artists/pull/1),
  [#2](https://github.com/dezolent/bounced-artists/pull/2) and
  [#3](https://github.com/dezolent/bounced-artists/pull/3) merged. Integration
  commit `40a35d345b2c88ae673c4bcb3929c17161a9d7df`; merged feature branches removed.
- Existing Railway web service follows `main`. Deployment
  `e80415fb-6165-47a0-9802-32c4561ff7d9` succeeded and `/ready/` returned ready.
  No new project or database was created. Repository visibility remains public.
- Django Admin and hashed static delivery, production configuration, anonymous API
  denial, membership-scoped UUID retrieval and interactive operator login verified.
  Custom user, seven domain apps / 67 domain models, additive migrations and
  constrained relationships are implemented. See architecture for runtime limits.
- Full suite passed on merged code: 53 tests including the local export bundle;
  GitHub CI passed for the importer (the private export is absent from CI).
- Daily PostgreSQL backups enabled after the operator's Pro upgrade. Recent
  completed backup observed before the import; PITR is off and no restore drill is verified.

## Applied Dezolent export

Production dry run had zero conflicts; explicit atomic apply succeeded. Repeat
dry run returned zero creates, updates and conflicts. Receipt and source snapshots
were read back; key counts persisted after redeployment.

| Read-back | Count |
| --- | ---: |
| Artists overall / managed | 24 / 3 |
| Recording identities | 38 |
| Releases (two declared EPs + one catalog-numbered single) | 3 |
| Release tracks (11 EP positions + one single track) | 12 |
| Pages / fanlinks | 89 / 40 |
| Source snapshots | 1,156 |
| Shared-identifier review items | 2 |
| Publication outbox entries | 0 |

The bundle contains 23 artist identities (reusing Dezolent/Morbeato while preserving
SRY4U), 477 source links, 16 lyric texts, three articles, 104 local asset references,
303 schema nodes and 85 source archive records. Original values are preserved in
snapshots; typed rows follow the import contract. Asset references are not uploaded
media. Existing artist UUIDs, management status and memberships were preserved;
production had no artist memberships assigned at read-back.

Manifest SHA-256:
`38f78791289819baaa9dee5f6be18bd6530d60e9fc98524f855b182838fbbd6f`.
The persisted receipt matched this hash and 1,156 source snapshots. No website,
Netlify build hook or publishing request was triggered.

## Open limitations and planned work

- `artists.bounced.studio` was explicitly authorized and attached to the existing
  Railway web service on 2026-10-20. Cloudflare has its DNS-only CNAME and Railway
  verification TXT. Public DNS resolves to the returned Railway target;
  ownership is verified and its certificate is valid. HTTPS currently returns
  400 from the old deployed application. The pending portal code adds the exact
  host/origin; the user must commit, push and deploy before the new login is live.
- Gone and Chasing Ghosts remain distinct despite shared ISRC/ISWC; two review
  items need editorial decisions. Website writer/rights data is unverified evidence.
- Event/analytics/rights/publishing tables do not imply external integrations,
  validated rights registrations, analytics collection, or website delivery.
- No external ingestion, Omkar scraper, MLC/PRO connector, Netlify delivery worker,
  Astro migration or provisioned R2 bucket is included. The local portal/public
  API implementation below has not been deployed.
- Assign real non-staff artist memberships; verify isolated backup restoration.

## Portal and public API working tree (2026-10-20)

Implemented directly on `main`, with no commit, push or PR, as requested. Existing
staged/unstaged documentation work was preserved. There is no implementation-plan
document for this change.

- Styled login, responsive overview/catalog/song/release journey, asset previews,
  streaming destinations and scoped editorial CRUD for profile/catalog/rights/
  events/pages/SEO/fanlinks. Owner/editor/viewer roles and explicit catalog grants
  are enforced independently of credits and organization membership.
- Private immutable originals, sanitized WebP previews, upload validation/limits,
  quota and R2 storage support. Production upload stays disabled until all four R2
  variables are configured; no credentials were retrieved or stored in Git.
- Staff-only signed-preview approval/revocation, immutable approved public API
  projections and exact nested public-field allowlists. No Netlify/Astro/outbox
  delivery action is triggered. Draft imports are not automatically approved.
- Additive migrations, schema documentation, dry-run/hash-checked import grant
  bootstrap, signed edit conflicts, audited removal/restoration and privacy tests.
- Full local PostgreSQL suite: **100 tests passed**, including the available private
  import bundle. Django checks, migration drift and static collection passed.
  Synthetic desktop/mobile visual and keyboard checks passed. No CI run for this
  uncommitted tree or production onboarding/upload verification is claimed.

Next milestone: user review/commit/push, controlled migration/deployment, private
R2 setup, real non-staff memberships and reviewed catalog assignments, then actual
interactive artist login and uploads at the custom hostname. Review source/rights
conflicts before public approval. Netlify delivery and Astro migrations remain
separate.

## Local blue branding and catalog seed (2026-10-20)

- Replaced green accents with the supplied wordmark's `#38bdf8` light blue and
  cooler surfaces. Login/sidebar use the unchanged transparent source PNG.
- Diagnosed the local empty state: artist/reference seeds existed, but no catalog
  import had been applied. Imported the verified export into loopback development
  PostgreSQL, then applied explicit persisted-UUID catalog grants for Dezolent.
- Local read-back: 38 recordings, three releases, 40 fanlinks, 89 pages, 107 asset
  references and 1,156 source snapshots. Copied all 104 hash-verified local source
  files into private local storage, with 80 sanitized image previews. All 38 song
  covers have local files; three remote references remain remote references.
- Repeat catalog dry run: zero creates, updates and conflicts. Repeat media dry
  run: zero copies needed. No users/memberships were invented or assigned; staff
  can browse the local catalog, while non-staff still require real memberships.
- Added a local-only, hash-reviewed `import_local_media` command and documented
  [repeatable seeding](operations.md#seed-the-complete-local-catalog). It rejects
  escaped/changed sources and conflicting editorial files/checksums. The catalog
  assignment command now includes explicitly mapped remote asset references.
- **105 PostgreSQL tests passed**, including the private export integration tests;
  Django/migration checks, static collection and JavaScript syntax checks passed.
  Live local login visually checked with the new palette and supplied logo.
  Changes remain uncommitted/unpushed; production data/configuration was untouched.

## Workspace selection and isolation correction (2026-10-20)

- The picker now lists only managed artists (locally Dezolent, SRY4U and Morbeato).
  Collaborators such as Bad Color remain usable as credits without being workspaces.
- Removed the portal's organization-wide staff catalog bypass. Explicit artist
  grants now scope staff and member catalog reads, child records, media, form
  choices and writes; global operator access remains in Admin/legacy artist API.
- Overview remembers the validated selection and carries the artist in navigation
  links. Switching keeps the section and safe collection filters, resetting page
  numbers; record details/forms switch to the corresponding collection. Invalid
  or revoked saved selections cannot restore access.
- Cards/release tracks show actual artist credits instead of relabeling every
  item with the selected workspace. Existing credits and assignments were preserved.
- Verified real local responses: Dezolent has 38 recordings, three releases,
  107 assets and 40 fanlinks. SRY4U and Morbeato have zero assigned catalog records;
  all 16 checked catalog/metadata families are empty there. Each selection persists
  on Overview, and asset switching stays on Assets. No memberships were invented.
- **115 PostgreSQL tests passed**, including ten workspace regressions and the
  private export integration tests. Django/migration checks and diff whitespace
  checks passed; read-only code review found no important issues. The local preview
  was restarted on port 8001. No production changes, commits or pushes were made.

## Asset categories, text previews and shared artwork (2026-10-20)

- Added optional allowlisted asset categories through additive catalog migration
  `0008`, available as dropdowns in private forms/API and filters in Admin. The
  asset table has category filtering, name/type sorting, a clickable Type header
  and filter-preserving pagination/workspace switching.
- Lyrics, robots.txt and other text use distinct reusable icons instead of broken
  images. Text details render escaped local content up to 256 KiB, with a full
  download and an explicit missing/truncated state. No external text is fetched.
  UTF-8 SRT/VTT/JSON/webmanifest uploads are supported as private attachments.
- Packaged all **32 supplied WebP logos** (18 social, 14 platform) in the shared
  static library. Every authorized workspace sees the same read-only library;
  platform and fanlink destinations reuse its logos. TikTok's packaged filename
  is normalized while the original imported `titkok` path remains intact.
- Hash-reviewed local `organize_assets` apply filled **65 blank categories** and
  removed **32 obsolete brand-logo grants**, with private receipts/audits. Exact
  imported brand paths are excluded from portal CRUD and future catalog grant
  bootstrap. Artist artwork and similarly named files elsewhere stay scoped.
- Local read-back: Dezolent **75 artist assets**, SRY4U/Morbeato zero; all three
  access **32 shared logos**. All **107 original Asset records**, **104 immutable
  file versions** and all 38 recording covers remain. All 16 lyrics have file
  icons and stored text; robots/manifest text details were checked. Repeat asset
  organization dry run has zero changes.
- **127 PostgreSQL tests passed**, including 12 asset regressions and the private
  bundle integration tests. Django/migration checks, static collection, JavaScript
  syntax and whitespace checks passed; read-only review found no important issues.
  Live local responses were verified with an authenticated Django client; the
  browser remained on login, so no authenticated browser visual check is claimed.
- Repeat local catalog dry run: zero creates/updates and **one preserved editorial
  title conflict** (`release:single:song:lost-love.title`), saved at 14:46 UTC before
  this change. It was not overwritten. Local preview restarted on port 8001;
  production was untouched, with no commits or pushes.

## Production catalog asset reconciliation (2026-10-20)

- Existing production R2 configuration was present and active. A fresh 124 MB
  PostgreSQL volume backup completed before writes. No credentials were read back,
  bucket access rules changed, infrastructure provisioned, or code committed/pushed.
- Baseline: 107 asset references, two operator-uploaded cover versions, and 105
  missing display names/categories/alt texts/descriptions. The separate dev database
  supplied 104 hash-verified originals; missing descriptions were completed from
  filenames and existing catalog/page relationships, without inventing visual claims.
- Applied a reviewed, source/UUID/organization-checked plan: copied 102 missing
  originals into private R2, generated validated image derivatives, and filled
  metadata on 105 assets. A separate MIME-only plan filled three blank remote WebP
  reference types. Remote references remain references; no external files were fetched.
- Read-back: all 107 assets have filename display names, categories, alt text and
  descriptions; zero filename mismatches or blank required metadata. There are
  104 stored versions/original objects, 80 image derivatives and 21 available text
  previews. Both operator-edited covers are unchanged, including their complete
  Asset rows/current version IDs. Visibility, rights approvals, 149 resource grants,
  memberships, 1,156 source snapshots and publication state were preserved.
- The final repeat dry run reported zero metadata updates and zero file copies.
  Server-side media checks returned 200 for an assigned preview, 302 for anonymous
  access and 404 for another artist. An actual authenticated browser showed the
  completed asset library and a stored robots.txt preview with a download link.
  SVG/other unsupported inline formats remain authenticated downloads.
- Private before/after plans and verification receipts are retained under ignored
  `artifacts/catalog-asset-sync/production-reports/`; the bounded result is
  `artifacts/catalog-asset-sync/summary.json`.
- Local code now adds the Visibility table column, allowlisted Private/Members/Public
  filter, visibility sorting, and filter-preserving navigation; image detail previews
  use saved alt text. **138 PostgreSQL tests passed**, with Django/migration checks,
  static collection and diff checks passing. These UI changes remain uncommitted on
  `main` for the user's manual review/push/deployment; only the data backfill is live.

## Catalog MVP working tree (2026-10-22)

Implemented directly on `main`; no commit, push, deployment or production write.
Existing asset reconciliation and library changes were preserved. `AGENTS.md`
now explicitly skips brainstorming approval gates and spec/plan documents for
this project; authorized changes are implemented and left for IDE review.

- Songs and releases each have one complete details page and one coordinated
  create/edit form: metadata, credits, labels, genres, identifiers, artwork,
  versioned lyrics/files, platform destinations, fanlinks and relationships.
  Related works sit below Appears on, text expands inline, and destinations use
  the packaged social icons. Supporting data indexes and selectors show readable
  related names and searchable metadata. Advanced data remains available.
- Added normalized labels, release groups, media, release genres, work identifiers,
  richer credits/edition metadata and explicitly scoped label/group grants.
  Additive migrations preserve existing UUIDs, source fields and legacy label
  parties. Removed tracks retain their original positions and UUIDs while active
  tracks can reuse those positions. Migration `0010` backfills historical removal
  audits and guards downgrade after slot reuse; see operations before deployment.
- Aggregate saves validate scope and the entire related graph, reject stale edits,
  preserve unchanged verification, audit reversible removals and roll back data
  plus newly stored files on failure. Switching a work and editing its nested data
  together is rejected: save the selection and reload before editing that work.
- Reviewed public schema v2 adds allowlisted catalog metadata and explicitly
  selected approved lyric text; existing schema v1 revisions remain readable.
  Private legal/contact fields, source payloads and authenticated media URLs stay
  excluded. Daily ingestion, automatic approval and website delivery remain future.
- `make verify` passed: **204 PostgreSQL tests**, Django checks, no migration drift
  and generated-schema consistency. JavaScript syntax and whitespace checks passed.
  The schema check now compares existing documents to generated model output
  without requiring a clean Git tree; a deliberately stale-file probe was rejected
  without modifying the file. Generated documentation covers **80 domain models**.
- Authenticated browser checks in an isolated synthetic local workspace verified
  song/release details, nested contributor/fanlink additions and save, text
  disclosure/download, validation feedback, and 390px mobile views without
  horizontal overflow. Synthetic test records/accounts are removed after the check.
  Additive migrations were applied only to the loopback development database.

## Catalog sorting and live filters (2026-10-22)

- Songs and releases sort by descending release date, with unknown dates last and
  stable title/UUID tie-breakers. Song type separates Solo/Originals,
  Collaborations and Remixes using active performing credits and explicit remix
  metadata; remixes take precedence over collaboration credits.
- Genre filtering uses assigned active relationships. Releases match their own
  genres or genres on visible active tracks. Search, editorial status, type and
  genre combine; pagination and workspace switches retain the selected filters.
- Catalog filters reuse the Assets request cancellation/debounced typing behavior.
  Dropdowns update immediately; results, counts and URLs refresh without replacing
  focused controls. Grid/list view survives refreshes, and GET forms work without
  JavaScript.
- `make verify` passed **212 PostgreSQL tests**, Django/migration checks and schema
  consistency. JavaScript syntax and whitespace checks passed. Authenticated
  synthetic browser checks confirmed date order, live dropdown/search updates,
  combined filters, focus retention, view switching after refresh and no overflow
  at 390px. Isolated browser fixtures were removed. Changes remain uncommitted on
  `main`; production was untouched.

## Portal user administration (2026-10-22)

- Added a superuser-only Users screen for searching accounts, creating non-staff
  artist users, editing profile/active status, and assigning or removing Owner,
  Editor and Viewer memberships across active artist workspaces in one form.
  No workspace selection is required to reach administration. Initial passwords
  use Django validation/hashing; invitation email is not implemented.
- Existing organization/resource-grant boundaries remain enforced. Staff and
  superuser accounts stay in Django Admin; inactive workspace assignments are
  preserved. Signed snapshots reject stale forms, unexpected privilege fields
  are rejected, and account/membership/audit writes roll back together on failure.
- `make verify` passed **224 PostgreSQL tests**, Django checks, migration drift
  checks and generated-schema consistency. Independent code review found no
  critical or important issues. Synthetic server-rendered desktop/mobile screens
  were checked; account creation, login, role changes, revocation and denied access
  were exercised by the Django suite. The isolated preview database was removed.
  No migrations, commits, pushes, deployment or production writes were performed;
  changes remain on `main` for IDE review.

## Portal publication and operations workflows (2026-10-22)

- Replaced the normal multi-step approval path with Save changes → Publish updates.
  Owners/editors preview and publish directly by default; staff can require review
  per workspace. Optional requests retain website/content selections. Staff can
  publish or return a request with a note shown to the artist. Explicit publication
  prepares readiness flags, safe destinations and confirmed artwork/lyrics rights
  atomically; the existing public API allowlist and immutable snapshots remain.
- Signed previews bind catalog sources, selected grants, policy, current published
  revision and request state. Concurrent changes require a fresh preview. A release
  cannot promote a draft group without edit access; only exact previewed safe
  platform matches are accepted. Ordinary saved edits preserve the old public copy.
- Added a unified staff Review queue for source issues and publication requests,
  with readable candidates, bounded safe evidence, resolve/dismiss/reopen decisions,
  return notes and preserved history. Resolving an issue does not manufacture a
  catalog match, replace source evidence or publish content.
- Operations covers **90 registered models**, including **85 domain models**, with
  explicit fields/actions and owning workflow links. Staff can manage reference and
  editorial data; superusers manage organizations and resource access. Source-backed
  rights assertions, evidence, uploaded versions, revisions and history are preserved.
  Eligible unassigned relationships can be removed/restored without deleting rows or
  inventing artist ownership. Workspace restoration reuses their original UUIDs.
- Users now includes profile/privilege/activation controls and validated password
  resets for system accounts, with last-administrator, stale-edit and audit guards.
  Passwords, credential URLs, source bodies, session keys and storage locators are
  withheld from operations display and forms. No invitation emails are sent.
- Full `make verify` passed **305 PostgreSQL tests**, Django/migration checks and
  generated-schema consistency. Independent publication/operations reviews found
  issues that were fixed with regressions, including request changes, source-claim
  preservation and credential-URL form redaction. Historical migration compatibility
  and threaded-test connection cleanup were verified. Synthetic publication/request
  submissions and rendered desktop/mobile screens were checked in disposable DBs.
- Three additive migrations add the review policy, request context/returned state
  and reversible relationship removals. No collectors, analytics engines, import
  processors or delivery workers were implemented or reported as running. API
  publication does not deploy or rebuild websites. Changes remain uncommitted on
  `main`; no production changes, commits or pushes were performed.
