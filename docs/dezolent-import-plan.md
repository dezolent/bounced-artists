# Import and integration contract

The Dezolent bundle importer is implemented. This document owns its preservation
contract and integration boundaries; [operations](operations.md#import-workflow-and-bounded-output)
owns runnable workflows and [status](status.md#applied-dezolent-export) owns applied counts.

The import reads the exported website bundle, validates every manifest byte count,
digest, row count, source reference and schema-node hash, then runs inside one
PostgreSQL transaction with a transaction-scoped advisory lock. Its default mode
executes the full import and rolls back; `--apply` commits only after verification.

`Website.theme_config.dezolent_import` stores source-key-to-UUID mappings, the
last imported field values used for three-way conflict checks, and a receipt.
The `dezolent-website` SourceRecord collector stores an immutable snapshot of
every JSON/JSONL record, including source archive text, using canonical JSON
SHA-256 hashes. Identical snapshots are reused; changed snapshots retain history.

The importer creates typed artist, catalog, rights-assertion, asset, page, SEO,
and fanlink rows only where source evidence supports them. It leaves imported
publishing rows in draft and does not create outbox requests. Shared ISRC/ISWC
assertions remain on distinct recordings/works and create review items.

The dry run reports planned creates, updates, and conflicts. Conflicts abort
`--apply`, preserving existing editorial values. Round-trip verification compares
all source payload hashes and expected relationship counts. No Astro, DNS, or
Netlify changes are part of the import.

## Identity and evidence rules

Reuse managed Dezolent/Morbeato and preserve SRY4U, memberships, UUIDs and edited
management status. The export's other identities are collaborators, not newly
managed artists. Match stable source keys/persisted UUID mappings rather than title
or ISRC. Gone and Chasing Ghosts remain distinct despite shared ISRC/ISWC.
Use album `source.songIds` order for EP positions; do not infer order from titles.
Preserve every original field, empty value, URL parameter, array order, lyric,
article body and SEO variant in source snapshots even where no typed field exists.
Rehydrate JSON-LD nodes by content hash; inferred writer credits stay unverified
source assertions. Source assets are references, not an authorization to upload media.

## Existing integration surface vs planned adapters

- Implemented: manifest-verified local export import, idempotent seeds, source
  snapshots, review records, receipt/hash/relationship checks and draft content.
- Persistence surfaces: Connection/SyncJob/SyncRun, metric/report observations,
  rights/event evidence, outbox and version records. Some rights evidence is
  imported and the transactional approval service exists; no scheduler, external
  ingestion adapter or publication delivery worker runs from these records.
- Future: authorized Spotify/SoundCloud/Apple/YouTube ingestion, Omkar scraping,
  MLC/PRO and event adapters, published-content snapshots/API, Netlify build hooks,
  Astro consumers, analytics dashboards and R2 media. No runnable commands exist
  for these features. Preserve existing Astro/Netlify, Cloudflare DNS and email
  systems until a specific task authorizes changes.
