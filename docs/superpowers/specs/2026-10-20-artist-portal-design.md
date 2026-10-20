# Artist portal and approved catalog API

Date: 2026-10-20. Status: approved for direct implementation on main. The user
requested no implementation document, commits or pushes. Deployment and real
artist onboarding are tracked separately in `docs/status.md`.

## Outcome and scope

Give each artist a polished workspace at `https://artists.bounced.studio/` to
understand and manage their catalog, recordings, releases, cover art, assets,
metadata and fanlinks. Opening the application root shows a designed login page;
successful login opens the workspace, with catalog cards leading to full song
details and all recorded streaming destinations. Owners and editors can create,
read, update and delete their authorized editorial records and assets.

Expose a separate anonymous, read-only approved-content API with explicit public
field allowlists and executable privacy tests. Private editing and public approval
are distinct operations. Netlify delivery, Astro migrations, external ingestion,
streaming-service synchronization and analytics collection are outside scope.

“Root” means `/` on the application hostname, including its existing Railway
hostname. The main `bounced.studio` website is not being replaced.

## Evidence and constraints

Read `AGENTS.md`, `docs/status.md`, relevant operations and architecture sections,
the private API, access tests, catalog/publishing/rights/provenance models and
importer code before proposing this design.

- The dated import evidence reports 38 recordings, three releases, 40 fanlinks,
  89 pages and 1,156 preserved source snapshots. Those are historical production
  counts, not dashboard constants or a fresh database read-back.
- Imports leave recordings, releases, pages and fanlinks in draft. Asset rows can
  already have `visibility=public`; that flag alone is insufficient publication
  consent. Platform matches are pending; rights assertions are unverified.
- Asset rows contain references, not uploaded media. Repository paths do not
  become browser URLs. Original source payloads must remain preserved.
- Catalog organization ownership, artist memberships and credits are separate.
  Organization membership, matching identifiers and credits must not grant access.
- Last dated evidence reported no artist memberships. Real onboarding and explicit
  catalog assignment are acceptance requirements, not implied by a working login.
- Fresh connector inspection on 2026-10-20 found the existing Railway web service
  live on `main`, deployment `e80415fb-6165-47a0-9802-32c4561ff7d9` successful,
  with no staged changes and no custom domain. Cloudflare zone `bounced.studio`
  is active; its exact `artists.bounced.studio` DNS query returned no records.
- The checkout already contains staged and unstaged documentation/tooling work on
  `codex/agent-workflow-docs`. Preserve it; implementation must use a focused
  `codex/` branch and stage only task-owned changes. No worktree is needed for
  this design-only change.

## Architecture choice

Use Django templates, responsive CSS and small progressive-enhancement JavaScript
modules, existing Django sessions, DRF and PostgreSQL. Add a `portal` app for
screens/forms; put authorization and mutation services in their owning domain
apps so HTML and API routes enforce the same rules. Bundle static files through
the existing WhiteNoise build. Use local fonts/system fallbacks and code-native
icons; no third-party scripts or tracking on private screens.

A separate SPA would add an authentication/build/deployment boundary without a
current need. Reskinning Admin would not provide the requested artist journey.
Retain `/admin/` for staff operations; it is not the artist landing experience.

## Experience and visual direction

Use near-black ink surfaces, warm white typography, restrained electric-lime
actions, generous spacing, large square cover art and subtle borders. Typography
and artwork carry the visual hierarchy. Controls have clear labels and generous
targets; status also has text and is never conveyed by color alone.

| Screen | Content and next step |
| --- | --- |
| Login `/` | Bounced identity, editorial visual panel, concise welcome, username/password labels, show-password control, inline errors and one clear sign-in button. No catalog or account-existence disclosure. |
| Overview `/app/` | Artist switcher, greeting, real catalog/asset/fanlink totals, recent music, missing-artwork and metadata tasks, recent edits. Each tile links to its filtered records. No invented stream counts. |
| Catalog `/app/artists/<id>/catalog/` | Search by title/identifier, recordings/releases switch, artwork card grid and list toggle, filters, pagination and add actions. Clear title, artist, release date precision and editorial status. |
| Song `/app/artists/<id>/recordings/<id>/` | Cover, title/version, credited display artists, date, duration and explicit flag; tabs for overview, metadata, assets, links/fanlinks and related work. Breadcrumbs and edit/add actions retain artist context. |
| Release detail | Cover, release metadata, ordered discs/tracks and song links; edit track positions without changing recording identity. |
| Assets | Filterable thumbnails/file list, upload progress, replace/version, preview, download, link/unlink, metadata/rights and removal. Show missing references honestly. |
| Fanlinks | Draft/approved status, linked song/release, ordered enabled destinations, preview and CRUD. Approval never triggers a website deployment. |
| Artist profile and data | Profile, aliases, genres, social/profile links, permitted contacts, related works/rights, events and imported editorial page/SEO content, with scoped forms and source-status labels. |

Navigation: Overview, Catalog, Assets, Fanlinks, Artist data. Artist data groups
secondary rights/events/page screens rather than crowding the main music journey.
On mobile use a collapsible navigation and single-column details. Search/filter
state is in the URL; browser back restores the catalog context. Dialogs have
focus management, escape/cancel and return focus. Forms work without JavaScript;
enhancement adds tabs, previews, progress and confirmation feedback.

Cover preference is recording primary cover, then a linked release primary cover
only if the viewer may access that release and asset, then a deliberate title
placeholder. Set intrinsic image dimensions and lazy-load below-the-fold artwork.
Display all stored song links privately, grouping pending/rejected links separately
from accepted links. Distinguish full-song, video, excerpt and playlist relations;
a playlist containing a song is not its direct streaming destination. Do not
invent links for absent services. Open external links safely with a clear label.

## Authentication and record access

Use Django's authentication views/forms with custom templates. GET `/` renders
login for anonymous users and redirects authenticated users to `/app/`. Preserve
only validated same-origin `next` paths. Login rotates the session; logout is POST
with CSRF. Require active accounts, secure cookies in production, generic login
errors and a database-backed login attempt limiter. Store limiter keys as hashes,
expire them, and never log passwords or full credentials. Password reset/email
delivery is not implied by this feature; do not show an inoperative reset link.

Users with one artist open that workspace; users with several choose an artist;
users with no memberships see a useful access-pending state with no catalog data.
Staff may use a staff-only selector; every artist account remains non-staff.

Add `ArtistResourceGrant` with an artist, same-organization resource, capability
(`read` or `manage`) and a typed exactly-one foreign key to recording, release,
asset, work, party or event. Unique resource/artist constraints prevent duplicate
grants. Websites already have an explicit artist relation; their pages and fanlinks
inherit that scope. Validate every target and nested relationship server-side.
Membership plus an explicit grant is required; a grant without membership is inert.

| Actor | Read | Write | Approval/access administration |
| --- | --- | --- | --- |
| Anonymous | Active approved public projection only | None | None |
| Artist viewer | Assigned records and member-visible fields/assets | None | None |
| Artist editor | Same as viewer | Assigned `manage` records and child records; create within selected artist | Submit for review only |
| Artist owner | Same as editor | Same editorial CRUD, including delete/archive actions | Submit for review; no user/membership/organization administration |
| Staff | Global trusted operations | Global editorial management | Review, approve, revoke and assign catalog resources |
| Superuser | Global | Global | Existing user/membership administration |

Model permissions and organization membership never widen an artist queryset.
Scope before UUID lookup and return 404 for inaccessible objects. A viewer attempting
a write to an otherwise visible object receives 403. Recheck membership/grant on
every request, including downloads, form choices and mutation transactions.

Creating a recording/release/asset in an artist workspace assigns an explicit
`manage` grant to that artist atomically. Selecting a related record requires its
own access check; never accept client organization, owner, publication status,
source mapping or arbitrary grants. Credits can show approved public display names
of collaborators without exposing their private profile. Do not mutate shared
platform objects, genre/service definitions or another artist's party through a
child form; create a local relation or request staff review.

Review/verification flags, asset rights approval, registration verification and
publication state are staff-owned fields. An artist can supply an assertion,
rights description or proposed link; cannot mark it accepted/verified by submitting
hidden fields or writing to a nested endpoint. Public selection always requires
the separate staff review described below.

For existing imports, staff review a dry-run assignment report derived from stable
persisted import mappings and website ownership. No title/ISRC-based assignment or
automatic grant from credits. Apply only reviewed exact IDs, with audit and an
idempotent assignment command. Do not grant all organization records to every member.

Contacts marked `private` remain staff-only; `members` and `public` contacts may be
managed within their artist workspace. Neither becomes public API content. Shared
rights/legal-party records require their own grant, not just a recording link.

## Editorial CRUD and preservation

Add authenticated `/api/portal/v1/artists/<artist_uuid>/...` endpoints and matching
HTML forms. Keep existing `/api/v1/artists/` read-only for compatibility. Private
serializers also use explicit fields. POST creates, GET lists/retrieves,
PATCH updates, DELETE removes; HTML writes use CSRF-protected POST forms.

| Resource | Editable surface | Delete semantics |
| --- | --- | --- |
| Artist profile | Name, slug, biographies, location, timezone; aliases/genres/profile links and visible contacts through child endpoints | Delete child rows; archive artist workspace by owner/staff, preserving identity/memberships |
| Recording | Display metadata, identifiers as assertions, genres, credits, relationships | Remove active editorial record through archive/tombstone; preserve stable UUID and imported evidence |
| Release | Display metadata, identifiers, credits, ordered tracks and artwork | Archive release; remove track/link relations without deleting recordings |
| Asset | File versions, display label/description/alt text, rights basis, visibility and attachments | Unlink first; archive imported/referenced asset. Permanently delete only unreferenced user-created files/rows after confirmation |
| Platform links | Artist links, recording/release matches, proposed URLs and relationship types | Delete local match; shared service listing remains intact |
| Fanlink | Title, slug, target, ordered destinations, labels and enabled state | Archive imported fanlink; remove user-created draft when unreferenced |
| Page and SEO | Title, safe structured editorial fields, description, canonical URL, robots and artwork | Archive imported page; remove unreferenced user-created draft |
| Work/rights | Assigned work metadata, aliases, recording associations and artist-owned contributor/claim assertions | Remove own editorial assertions or archive assigned records; retained source claims remain evidence |
| Event | Assigned event metadata, venue/ticket associations and artist-local assertions | Archive imported event; remove own unreferenced draft |

Source snapshots, import manifests/mappings, observation history, audit events,
integration credentials, metrics observations and registration verification are
system/evidence data. They are not unrestricted artist CRUD tables. Show scoped
editorial values and evidence summaries where relevant; retain originals and record
corrections as editorial changes. A UI “Delete” action explains archival where
preservation is required and removes the item from active lists and public serving.

Add archive/tombstone fields where the current model lacks them; no destructive
migration or cascade deletion of imported entities. A delete/archive touching an
active public revision revokes that revision in the same transaction. References
prevent unsafe physical deletion and return a human-readable conflict.

All mutations use validated forms/serializers and atomic domain services, with
same-organization checks, server-owned grant creation and bounded audit records.
Use an integer revision and conditional writes to reject stale updates with 409
instead of overwriting concurrent edits. Preserve source UUIDs and field values
in snapshots. Import repeat behavior must report edited canonical fields as
conflicts rather than silently restoring them.

## Asset storage and cover art

Recommended production storage is a private Cloudflare R2 bucket through a Django
S3-compatible storage adapter. This is a proposed storage addition, separate from
the already authorized subdomain configuration; provisioning/billing and credential
setup must be resolved with design approval before production upload is enabled.
Use local temporary filesystem storage in tests/development, never ephemeral
Railway container storage as production media persistence.

Proxy uploads through authenticated Django endpoints in the first version so size,
type and access checks happen before storing. Initial limits: JPEG/PNG/WebP cover
images up to 20 MiB and 40 megapixels; WAV/FLAC/MP3/M4A audio up to 250 MiB;
PDF/TXT/CSV attachments up to 25 MiB. Enforce request and streaming byte limits,
content signatures, pixel limits and per-artist quota in the service, not only the
browser. Stream large uploads; clean temporary files on abort. Do not render
uploaded HTML/SVG/scripts inline. Sanitize filenames and use random immutable
storage keys. Original files remain private; generated raster previews strip EXIF.

Add immutable `AssetVersion` rows for storage key, size, checksum, detected type,
dimensions and creator, plus explicit display metadata on Asset. Replacement adds
a version; never overwrites a file referenced by an approved revision. Private
preview/download requests recheck access and stream from storage with no-store
headers; do not expose bearer download URLs in shared pages or logs. Large audio
downloads support authorized bounded Range requests. Audio preview is available
only for accessible playable files, not guessed from a source reference.

Imported repository-only references show a missing-file state with an upload action.
Existing external cover URLs may display privately after HTTPS/host validation,
with `referrerpolicy=no-referrer` and a restrictive image CSP. No server fetch of
arbitrary source URLs. Private storage-only covers require an asset grant. A
controlled local media-import command may accept an explicitly supplied directory,
validate manifest/hash/path containment and attach files by stable asset mapping;
it must not fetch private source repositories or assume missing files exist.

R2 remains private: neither `r2.dev` nor a public bucket domain is enabled. Public
artwork is served through the approved-content media endpoint after publication
and rights checks. No permanent public URL is produced from a raw `storage_key`.

## Approved-content API contract

Public endpoints live under `/api/public/v1/artists/<approved_slug>/`. Only GET,
HEAD and OPTIONS are enabled. Private authentication defaults remain unchanged;
anonymous permission is explicitly attached to these projection views only.

Introduce immutable `ApprovedCatalogRevision` and an active revision pointer per
Website. The revision contains schema version, monotonically increasing content
version, approver/time, checksum, validated public payload and pinned artwork
version references. Audit/organization/source IDs remain private model fields.
Artist scalar fields currently lack publication state; the revision is their
approval boundary, not `Artist.public_metadata` or imported flags.

Staff generate a preview from a typed builder, review exact output, then approve
that preview only if its source revision/checksum is unchanged. Lock the website
and check row revisions in one transaction, create the immutable revision and
move its active pointer atomically. Artist owners/editors may request review but
cannot self-approve or change pointers. Public views serialize the approved
payload only, never live nested ORM serializers. Later edits do not silently
change public content. Revoke clears the pointer immediately. Approval is local
API publication and does not call `approve_page_and_request_publication`, enqueue
an outbox entry or make any delivery/network request.

Eligible recordings/releases must be explicitly selected and approved, belong to
the website's organization and have an explicit assignment to its artist. Release
tracks reference only recordings included in this revision. Fanlinks must be
approved, enabled as applicable, belong to this website and target included
recordings/releases. Accepted matches and manually reviewed HTTPS destinations
are required for public links. Pending/rejected matches never qualify. Unknown
availability remains unknown, not a claim that a service was checked recently.

Artwork additionally requires `visibility=public`, staff-confirmed rights basis,
an explicit publication selection and a validated immutable uploaded raster
version. An imported public flag or an arbitrary external/repository path is
insufficient. Missing artwork is null, and does not force publication of a source
URL. Revoking rights, changing visibility to private or deleting an included asset
revokes affected active revisions atomically. Public media requests recheck the
active revision and current revocation state.

The following key sets are exhaustive, including nested objects. All scalar text
is plain text, never source HTML/JSON-LD. Unknown keys are rejected at builder and
response validation boundaries. Optional fields are explicit nulls or empty arrays.

| Object | Exact public keys |
| --- | --- |
| Response envelope | `schema_version`, `content_version`, `artist`, `results`, `next` (collection routes); detail routes replace `results`, `next` with the named resource |
| Artist | `id`, `name`, `slug`, `bio_short`, `bio_long`, `location`, `artwork`, `profile_links` |
| Recording | `id`, `title`, `version_title`, `recording_type`, `duration_ms`, `explicit`, `original_release_date`, `date_precision`, `artists`, `genres`, `artwork`, `platform_links`, `release_ids`, `fanlink_ids` |
| Release | `id`, `title`, `release_type`, `release_date`, `date_precision`, `artists`, `artwork`, `tracks`, `platform_links`, `fanlink_ids` |
| Display artist credit | `id`, `name`, `role`, `billing_order` |
| Genre | `name`, `slug` |
| Track | `recording_id`, `disc_number`, `track_number`, `display_title` |
| Artwork | `url`, `alt`, `width`, `height`, `mime_type` |
| Platform destination | `service`, `label`, `url`, `relationship_type` |
| Profile link | `service`, `label`, `url` |
| Fanlink | `id`, `slug`, `title`, `target_type`, `target_id`, `artwork`, `destinations` |

`id` values are opaque stable public UUIDs mapped server-side to canonical rows;
they are not import keys. API resource IDs remain stable across reapproval. Public
slug lookup and all nested contents come from the active revision. The same actor
receives the same public projection regardless of authentication. Public queries
cannot select draft revisions or expand private relations.

`date_precision` is `unknown`, `year`, `month` or `day`; date values are null,
`YYYY`, `YYYY-MM` or `YYYY-MM-DD` accordingly. Do not imply a January 1 release
when only a year is known. `duration_ms` and `explicit` remain nullable.
`service` is a reviewed service slug. URLs require HTTPS, a service-specific exact
host allowlist (or reviewed Bandcamp subdomain), permitted path/locale forms and
per-service permitted query keys only. Reject userinfo, local/private hosts,
nonstandard ports, token-bearing queries and unreviewed fragments. No redirects
are followed server-side. Artwork URL is an application media endpoint containing
an opaque public identifier, never the source URL/storage path.

Never expose contacts, emails, legal names from rights tables, memberships,
organization identifiers, management status, timezone, internal timestamps,
verification evidence, ISRC/ISWC assertions, private notes, sources/archives,
`content_json`, arbitrary `public_metadata`, SEO structured-data overrides,
audit details, metrics, credentials, repository paths, storage keys or signatures.
An approved biography/display name still requires human editorial review for
private text; key allowlists alone cannot detect sensitive prose.

| Route suffix | Result |
| --- | --- |
| `/` | Approved artist detail |
| `/recordings/`, `/recordings/<public_id>/` | Approved recordings |
| `/releases/`, `/releases/<public_id>/` | Approved releases with ordered tracks |
| `/fanlinks/`, `/fanlinks/<approved_slug>/` | Approved fanlinks and destinations |
| `/artwork/<public_asset_version_id>/` | Selected raster bytes for active revision |

Use 404 for absent/revoked artist/resource/artwork and 405 for public mutations.
Collections use 50-row pages with a maximum of 100, deterministic ordering and
allowlisted title search/type filters within the active revision. `next` is an
application-relative URL with validated parameters. Responses and public media
start with `Cache-Control: no-store`; privacy/revocation takes precedence over
shared caching in this version. No CORS expansion, draft preview query parameter
or automatic public fanlink page delivery is included.

## Privacy, access and behavior tests

Implement tests against the real PostgreSQL test settings with synthetic fixtures;
never commit raw private exports. Assert exact key sets at every nesting level,
not only the absence of a few field names.

1. Draft, unselected, archived and revoked rows are absent from public lists and
   detail/media routes. An imported asset marked public without rights approval
   remains absent. Every public write method fails without mutating data.
2. Inject distinct sentinel values into contacts (including public contacts),
   party legal names, source JSON, notes, audit records, metadata dictionaries,
   identifiers, storage paths and token-like URLs. Assert none appears in public
   response bytes, nested payloads, errors, redirects or headers.
3. Assert exact response schemas for artist, recording, release, credit, artwork,
   track and fanlink; adding a model field cannot expand the public contract.
   Raw nested JSON and source HTML never bypass the builder.
4. Draft child links/credits/tracks, private artwork, cross-organization joins,
   unaccepted matches, malicious URL schemes/hosts/queries and private contact
   fields are rejected or omitted as specified. Opaque public IDs do not unlock
   private endpoints. Gone and Chasing Ghosts stay distinct despite shared IDs.
5. Editing live rows after approval leaves public bytes unchanged; reapproval
   changes content version; stale preview approval fails; rollback/revocation
   removes all related endpoints. An edit/approval failure rolls back atomically.
6. Membership and grant matrix covers two artists within the same organization,
   a second organization, every role, staff, inactive users, membership/grant
   revocation and users with model permissions only. Credits, org membership and
   shared ISRC never grant access. Check list counts, search, form choices,
   nested writes, uploads, previews/downloads and direct UUID requests.
7. Test valid CRUD for every resource family, wrong-owner relationships,
   protected/shared deletes, archived exclusion, source preservation, audit,
   revision conflicts and rollback. Editing private data never auto-publishes it
   or creates an outbox entry. Existing read-only API behavior remains covered.
8. Test root login, safe next redirects, CSRF on login/logout and CRUD, session
   behavior, attempt limiting, no-membership onboarding, artist switching, and
   non-staff denial of Admin/access administration.
9. Test upload limits/type mismatches, traversal names, oversized pixel images,
   inaccessible assets, immutable replacement, aborted uploads, cleanup, ranges,
   and rights/visibility revocation. Assert storage credentials are never rendered.
10. Browser journeys cover login → overview → catalog → song → every detail tab,
    artwork and streaming destinations, CRUD/save errors and delete confirmation.
    Review desktop/mobile layouts, keyboard access, contrast, focus, overflow,
    loading/empty/missing-art states and no JavaScript operation. Use synthetic
    fixture artwork for screenshots and keep real private views out of artifacts.

Run relevant focused tests while implementing, then `make check`, `make test`
and static collection. Regenerate schema docs after model changes and inspect
generated diffs. Because existing generated docs already have uncommitted changes,
`make schema-check` may fail against HEAD until intended documentation changes are
committed; distinguish that from generator inconsistency. Any importer/grant
bootstrap changes also require `make test-import BUNDLE=<actual path>` and a clean
repeat dry run with an explicitly supplied bundle. Do not skip PostgreSQL or
report skipped bundle tests as verified. CI must pass for the final PR tree.

## Domain, deployment and onboarding

Reuse the existing Railway project/environment/web service from operations. Once
the login/dashboard code is verified and deployed, attach the exact custom domain
`artists.bounced.studio` to that service, read Railway's returned DNS requirements,
then create only the corresponding Cloudflare hostname records. Never guess the
CNAME from an existing generated hostname. Begin DNS-only for direct origin/TLS
validation; do not change zone-wide SSL, apex/www, mail, nameservers or unrelated
records. Recheck for conflicts immediately before writing. Additional Railway
ownership-verification records are confined to that hostname's returned requirements.

Include the exact hostname in allowed-host configuration and its HTTPS origin in
CSRF settings, preserving existing values without printing secrets. Use an
in-container names-only/membership check or controlled configuration edit rather
than dumping Railway variable values. Verify Django deployment checks, an actual
HTTPS GET of `/`, `/ready/`, collected CSS and successful interactive login. Test
logout, CSRF-protected editing, nonmember denial and absence of private data from
anonymous endpoints. Check Railway certificate/domain status, not just DNS lookup.

Before additive migrations verify a completed database backup, review the migration
plan and serialize deployment through the existing predeploy process. No DB reset,
renaming, fake migrations or seed identity replacement. Roll back code if needed;
additive schema/evidence remains. Revoke public revision pointers if approval
content needs withdrawal; do not use database restoration for a content rollback.

Create real artist accounts/memberships through existing superuser-controlled
onboarding, with passwords entered interactively by the operator. Determine actual
user-to-artist mappings from the operator; do not infer identities or invent
passwords. Apply reviewed catalog grants and check owner/editor/viewer behavior.
Ship upload controls only when durable storage credentials/configuration are in
place. A dashboard on the generated hostname is useful for verification but does
not satisfy the requested custom-domain delivery.

## Delivery boundaries and acceptance

Implement in separable reviewable increments: (1) access/grants and public
projection contract/privacy tests, (2) login/overview/catalog/song/release journey,
(3) complete editorial CRUD and durable media, (4) live routing and onboarding.
Each increment uses the same authorization/mutation layer; none authorizes Netlify
or Astro work. This sequence is a delivery outline, not the implementation plan.

The request is complete only when the new root login and cohesive dashboard work
on the custom hostname; actual assigned artists can browse their imported music
and available cover art, see all stored streaming destinations, exercise permitted
CRUD across their editorial data and assets, and the independent approved API
passes its privacy tests. Missing source files and unavailable metadata must be
visible as such, with upload/correction actions. Accounts, mappings and storage
setup needed for this final check must be resolved before claiming completion.

## Source references for storage/network choices

- [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/):
  per-record DNS-only versus proxy behavior; implementation should recheck current
  Railway custom-domain instructions before deciding proxy settings.
- [Cloudflare R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/):
  private defaults and the independent public access paths that must remain off.
- [Cloudflare R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/):
  signed URLs are bearer credentials; use authenticated application delivery for
  immediate membership/revision revocation in this design.
