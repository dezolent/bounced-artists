# Dezolent bundle import plan

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
