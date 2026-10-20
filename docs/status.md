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
