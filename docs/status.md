# Current status and evidence

Last verified **2026-10-20**. This is a dated record, not a live monitor. Recheck
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
