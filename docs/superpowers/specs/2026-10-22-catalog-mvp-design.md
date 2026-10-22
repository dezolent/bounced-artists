# Catalog MVP: songs and releases

Date: 2026-10-22. Status: approved reference. Implementation evidence is in `docs/status.md`; future
work follows `AGENTS.md` without spec or approval gates.

## Intended outcome

Artists manage their catalog through two primary journeys: Songs and Releases.
Every item has one complete details page and one complete create/edit form.
Artists can inspect, add, change and remove metadata, credits, artwork, lyrics,
files, platform destinations and fanlinks without assembling rows in separate
entity screens. Artist websites consume the approved catalog through the existing
public projection boundary.

The user expects future daily scripts to populate most catalog data. This change
must leave that possible while making manual corrections easy. Building those
collectors or scheduling them is a separate milestone; no working integration is
implied by this design.

## Current evidence and constraints

Inspected the current working tree on `main`, including `portal/resources.py`,
`forms.py`, `services.py`, `views.py`, `querysets.py`, both catalog detail templates,
asset display helpers, catalog/rights models and public projection allowlists.

- The recording screen has separate Overview, Metadata, Assets, Links and Related
  work tabs; the release screen also uses tabs.
- Generic recording/release forms edit basic scalar fields. Related records are
  edited through independent resource screens.
- Release already has `label_party`; recording has no label relationship.
- Recordings, works, ordered release tracks, identifiers, credits, genres, assets,
  platform matches and fanlinks already have separate records.
- Generic resource labels fall back to Django object strings for relationships.
- Validated private media, versioned uploads, escaped bounded text previews and
  packaged platform logos already exist and should be reused.
- Public catalog payloads currently exclude identifiers, lyrics and label data.

Existing staged and unstaged asset/portal changes belong to the user and must be
preserved. Work remains uncommitted on `main`. Schema changes are additive; stable
UUIDs, original source fields, source snapshots, grants and editorial edits stay
intact. Current architecture and operations docs remain authoritative for access,
storage and deployment.

## Approach and alternatives

**Recommended: normalized catalog with task-focused screens.** Keep distinct
musical entities and build a coordinated editor over their relationships. Add
the missing catalog concepts incrementally. This supports both manual editing and
future ingestion without exposing database assembly as the artist workflow.

Two alternatives are less suitable:

- Flatten each song or release into a large JSON document: faster initially, but
  duplicates shared works/credits and complicates identity, permissions and merges.
- Replace the database with the complete MusicBrainz schema: much larger migration
  and application rewrite than the requested MVP needs.

The recommendation is conceptual alignment with MusicBrainz, not an identical
table-for-table clone or a claim of complete schema compatibility.

## Primary navigation and catalog

Catalog remains the main navigation destination, with clearly named Songs and
Releases collections and Add song/Add release actions. Artwork, title, credited
artists, type, date and readable duration appear in the collection. Search and
workspace selection remain available. Full metadata lives on each details page.

Assets and fanlinks remain accessible as libraries. Artist profile remains
available. Supporting entity lists move under an expandable Advanced data area;
existing URLs remain usable. Events, registrations and rights administration are
preserved but are not prerequisites for catalog editing.

## Song details: one continuous page

No tab hides any section. Optional anchor links scroll to sections.

1. **Header:** cover art, title/version, primary and featured artists, type,
   release date, readable duration and Edit song/Remove actions.
2. **Recording details:** all editable song metadata, labels, genres, explicit
   flag, identifiers and verification state. ISRC and recording MBID appear here;
   ISWCs and work MBIDs are shown with their work names and provenance of the
   relationship. Missing values say Not set. Partial dates retain their precision.
3. **Artists and collaborators:** ordered credits with roles and credited names.
4. **Appears on:** linked releases. **Related works** sits directly underneath,
   showing composition titles, ISWCs, work identifiers and contributors visible
   to the current artist.
5. **Listen on:** responsive destination cards using the packaged service logos,
   service names and external links, following the attached screenshot. Full song,
   video, playlist and excerpt links remain distinguishable. Unsupported services
   use an accessible generic icon. Only stored valid URLs become links.
6. **Fanlinks:** every associated fanlink and its ordered destinations, with
   add/edit/remove controls available from this item workflow.
7. **Lyrics and files:** cover previews and every attached file with readable
   names, categories, attachment roles, edit/replace/detach actions and downloads
   when a stored original exists. Each text attachment has a keyboard-accessible
   Expand/Collapse disclosure rendering escaped text inline. Reference-only,
   missing and truncated content states are explicit.

Editing a section from details opens the same complete editor at that section.
Artists never need an entity index to finish a normal song edit.

## One create/edit form

Use a single multipart form with labeled sections, repeatable rows, one Save
action, Cancel and a persistent summary of validation errors. Submitted values
and child-row errors remain visible after failure; uploads that must be selected
again are identified explicitly.

Song sections cover:

- Title/version, type, date/precision, human-friendly duration, explicit flag,
  structured descriptive metadata and optional editorial notes.
- Existing or newly created labels, ordered primary/featured/collaborator credits,
  credited names, roles and genres. Create missing collaborators, labels and genres
  inline, within permitted workspace ownership; names never grant catalog access.
- Repeated identifier rows with explicit entity type. Recording ISRCs/MBIDs stay
  separate from composition ISWCs/work MBIDs. No uniqueness rule merges recordings
  globally by ISRC, ISWC or title.
- Related works, contributors and release appearances. Creating a composition is
  optional; a recording can exist with no known work or release.
- Cover selection/upload, attachment roles, other uploads and existing assets.
  Plain text lyrics can be typed or uploaded. Text edits create a new immutable
  file version, using the normal media validation path. The page identifies the
  lyric asset being edited and preserves other lyric variants and originals.
- Platform service, URL, relationship type and optional external identifier.
  Add/change/remove rows directly. Metadata imported for a shared platform object
  is preserved; changing one association must not silently change another item.
- Fanlink website, slug/title and ordered destination rows with enable/disable,
  add/change/remove controls. Creating a fanlink needs an existing authorized
  Website; a missing Website produces an actionable explanation, while the rest
  of the song can still be saved.

Private imported evidence is inspectable where already permitted, not presented
as arbitrary editable JSON. Operational timestamps, checksums, verification and
approval fields remain controlled by existing services. “All metadata” means
catalog metadata and its relationships, not credentials or system internals.

## Releases

Apply the same single-page view and coordinated editor to releases: artwork,
title, type, date, label(s), genres, credits, identifiers, files, destinations and
fanlinks. Include release group, edition metadata, barcode/catalog number,
country, language/script, packaging and ordered media/tracks where known.

The release editor can attach existing songs or create a song inline, set disc
and track positions, track display titles/credits and track lengths, and detach
tracks. Reordering must support position swaps without transient uniqueness
failures. Opening a track reaches its complete song page.

Release duration is derived from known track lengths and marked incomplete when
some lengths are missing. Release details show related works through their tracks;
ISRC/ISWC values remain attached to recordings/works, not misclassified as album
identifiers. Detaching or removing a release does not delete its recordings.

## MusicBrainz alignment and additive model changes

MusicBrainz distinguishes recordings, tracks, works, releases, release groups,
artist credits, labels and genres. Its full schema page was rate-limited during
inspection; the entity documentation below was available directly or through
indexed official documentation. Recheck exact schema details before migrations.

| Concept | Proposed handling |
| --- | --- |
| Recording | Preserve `Recording` and UUID; add bounded descriptive fields as needed. ISRC/recording MBID remain typed identifiers. |
| Work | Preserve `MusicalWork`/`RecordingWork`; add typed work identifiers for MBID and multiple ISWCs while retaining existing `iswc` values. |
| Release group | Add a distinct catalog grouping for editions, with primary/secondary types and optional release-group MBID. Existing release types remain preserved during transition. |
| Release | Preserve `Release` identity; add optional group and edition fields. Never infer edition equivalence from titles. |
| Medium and track | Add explicit medium records and nullable links from existing tracks. Preserve disc/track numbers; add track-specific credits, length and optional track MBID. |
| Artist credit | Preserve existing credit UUIDs/roles/order; add credited-as names and join phrases where needed. Roles and appearance credits remain distinguishable. |
| Label | Add a catalog Label identity for an imprint, with optional MBID and optional link to a rights Party. A label name is not a legal-name disclosure. |
| Label association | Add ordered ReleaseLabel and explicit RecordingLabel relationships, including release catalog numbers. Preserve `Release.label_party` as legacy evidence; no blind conversion of arbitrary parties into labels. |
| Genre | Reuse Genre/RecordingGenre and add release/release-group genre associations as needed; show explicit release genres separately from track-derived genres. |
| URLs and files | Retain platform objects/matches, fanlinks and versioned assets as local extensions. |

On recordings, show explicitly associated labels first and release-derived labels
separately with their release names. Selecting a song label never rewrites a
release label. Permit multiple labels and a clear primary association. Label
creation/selection uses explicit scoped grants like other editable shared catalog
resources; matching organization alone is insufficient authorization.

Additions begin nullable/optional. Any deterministic legacy backfill must be
idempotent and preserve original fields; ambiguous mappings remain unmapped.
Do not invent MusicBrainz IDs or create a release for every standalone recording.

Official references:

- [Requested database schema](https://musicbrainz.org/doc/MusicBrainz_Database/Schema)
- [Recording](https://musicbrainz.org/doc/Recording)
- [Work](https://musicbrainz.org/doc/Work)
- [ISWC](https://musicbrainz.org/doc/ISWC)
- [Release group](https://musicbrainz.org/doc/Release_Group)
- [Label](https://musicbrainz.org/doc/Label)

## Readable entity lists and choices

Give each existing resource an explicit row presentation, related-field search
and appropriate eager loading. Apply readable labels to details, dropdowns,
validation errors and removal confirmations as well as index pages. Examples:

- Song genre: `Lost Love — Electronic`, with the recording linked separately.
- Artist party: `Dezolent — Example Label`, plus relationship type.
- Song identifier: `Lost Love — ISRC: …`, with verification status.
- Credit: `Lost Love — Featured Artist`, plus role and billing order.
- Release track: `Example EP — Disc 1, Track 2 — Lost Love`.

Use explicit allowlisted columns per entity rather than dumping all model fields;
private legal/contact fields retain existing restrictions. No index falls back
to `Model object (UUID)` as its primary description.

## Save, removal and conflict behavior

Implement a catalog editing service coordinating scoped parent and child forms.
Validate the complete submission before mutation, then lock authorized records,
recheck membership/grants and signed revisions, and write in one transaction.
Child-only edits must participate in conflict detection, including edits from
legacy screens and later import processes. Reject foreign child IDs rather than
treating them as new records. Read-only relationships remain visible but disabled.

Reuse existing audits, unverified/pending transitions and reversible removal.
Never replace all children by deletion/recreation. Removing an attachment or
credit removes the association; it does not destroy a shared underlying asset,
artist, work or label. Show affected release appearances and fanlinks before
archiving a song. Keep originals, source evidence and stable IDs recoverable.

Database transactions do not roll back object storage. Validate and stage uploads
before committing their references, publish no references to failed uploads, and
clean up newly staged unreferenced files after rollback. Never remove originals.
Bound upload totals and repeatable rows using existing limits plus explicit
aggregate limits. Text previews remain bounded; truncated previews are never used
as the source of a replacement file.

## Website delivery and later ingestion

Extend the reviewed projection with only allowlisted public catalog metadata:
display labels, selected public identifiers, approved lyrics/assets, genres and
credits. Shared party legal names, contacts, source payloads and private download
URLs remain excluded. Public asset delivery uses the established approved-media
contract, not authenticated portal URLs. Preserve compatibility for existing
projection revisions; version the expanded schema and test both old and new
payloads before exposing it.

Portal saves remain drafts under the existing review policy. They do not imply
instant website changes. Website integration and automatic approval/delivery
policy need their own explicit milestone; this work prepares the catalog/API and
does not modify artist website repositories or provision infrastructure.

Future collectors must use stable provider keys and persisted mappings, retain
source observations, respect editorial overrides and removal markers, and queue
conflicts rather than overwrite artist corrections. These are ingestion
requirements, not a claim that collectors or override reconciliation are running.

## Acceptance and verification

- A song and release each expose every supported section without changing tabs;
  related works appear below Appears on for songs.
- Create/edit either item with credits, genres, labels, identifiers, artwork,
  lyrics, attachments, platform links and fanlinks in the same workflow.
- Release editing supports existing/new tracks, multiple discs and reordering.
- Text expands inline, downloads preserve full originals, and replacement edits
  preserve version history. Missing and oversized text cannot be silently lost.
- Entity indexes and selectors display meaningful related names and search them.
- Viewer/editor/owner behavior, cross-artist UUID attempts, read-only children,
  stale child revisions, revoked grants and rollback failures are tested.
- Existing identifiers/source anomalies and imported UUIDs survive migrations;
  Gone and Chasing Ghosts remain separate. Repeat migration/backfill behavior and
  importer preservation are verified against available fixtures/bundles.
- Public projection tests prove nested allowlists and privacy for both supported
  schema versions; lyrics require existing publication/rights approval.
- Run `make verify` against the required local PostgreSQL, plus JavaScript syntax
  and authenticated desktop/mobile browser checks covering create, edit,
  validation failure, text disclosure/download and removal. Regenerate schema
  docs from models using the repository generator.

## Approved defaults

The proposed defaults are: normalized MusicBrainz-style entities behind simple
catalog screens; reversible removal; standalone songs allowed; daily ingestion
deferred; existing publication approval retained. The user approved this direction and requested direct implementation on `main`
without further spec, planning-document or brainstorming approval steps.
