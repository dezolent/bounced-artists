# Current status and evidence

Last verified locally **2026-10-22**; production evidence below retains its own date. This is a dated record, not a live monitor. Recheck
mutable facts only when the next task depends on them; use known IDs in
[operations](operations.md#railway-resources) instead of rediscovering resources.

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
