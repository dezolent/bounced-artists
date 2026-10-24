# Apple Music data options

Researched **2026-10-24**. This is a capability and cost assessment, not a deployed
Apple analytics integration. The user confirmed Apple Music for Artists access only. No Apple account, paid API
subscription or private artist session was connected during this work.

## Why Platform pulse has no Apple plays

Omkar's song/artist endpoints provide public catalog metadata. The stored song
contract includes artwork, release dates, artists/composers, identifiers, audio
traits and album information, but no worldwide play counter. Public Apple Music
catalog data does not expose that counter either. Missing audience counts must
remain unavailable rather than zero or an estimate derived from chart position.
Apple's [catalog API](https://developer.apple.com/documentation/AppleMusicAPI)
provides songs, albums, artists, playlists and charts. [MusicKit](https://developer.apple.com/musickit/)
adds playback and listener-authorized library/recommendation features; listener
sign-in does not authorize the application's access to an artist's business analytics.

## What Songstats and Viberate actually offer

[Songstats' Apple Music offering](https://songstats.com/platforms/applemusic)
tracks chart performance, playlist placements and editorial features. The current
[API documentation](https://developers.stats.company/songstats#operation/get-artist-stats)
was inspected in the rendered browser because its JavaScript documentation did
not render through the text fetcher. Its artist Apple Music example lists current/
historical playlist, editorial-playlist, charted-track and country coverage. The
[track statistics example](https://developers.stats.company/songstats#operation/get-track-stats)
also shows playlists and charts, with placement details, rather than Apple track
plays or listeners. These are documentation examples, not an authenticated query
for our three customers. Songstats requires a dedicated enterprise API key; API
access/pricing must be obtained from the vendor. A dashboard subscription does
not establish enterprise API entitlement. No request was sent and no key entered.

[Viberate's public Apple analytics](https://www.viberate.com/apple-music-analytics/)
focus on playlist presence, positions, curators and adds/drops. Its separate
[artist product](https://www.viberate.com/diy-musician/) advertises connecting
Apple Music for Artists and comparing Apple plays across songs; this is first-party
account data rather than public catalog scraping. Its [product announcement](https://www.viberate.com/platform-updates/)
explicitly distinguishes these connected accounts from public/social data.
The connection's underlying authentication/API mechanism is not disclosed in the
public materials reviewed, so we cannot conclude that Viberate uses a generally
available Apple Music for Artists OAuth API. Their artist page advertises $2.99/month;
the public analytics page advertises $19.90/month. Neither establishes a price or
license for feeding their connected artist data into Bounced Artists; that requires
confirmation from Viberate. No purchase or vendor message was made.

## Official API options and cost

| Option | Useful data | Access and cost | Fit for this application |
| --- | --- | --- | --- |
| Apple Music REST API | Authoritative catalog IDs, metadata, artwork, charts and known playlist contents | Developer token signed with a MusicKit key. Apple Developer Program is $99 USD/year, or local currency. No separate per-call tariff was published in the reviewed Apple documentation; hosting/integration still costs money. | Prefer direct REST for a future catalog adapter; it does not unlock global song plays. |
| MusicKit JS/Swift | Catalog plus playback and an authorized listener's personal music data | Same developer program; playback features depend on listener subscription/permissions | Unnecessary for a Django cron that needs catalog metadata and artist analytics. |
| Music Analytics API | Aggregated plays/listeners, engagement and audience dimensions | Separate music-provider/label-partner access in iTunes Connect; eligible Legal/Admin users configure keys and terms. No public self-service price was identified. Developer membership alone does not establish eligibility. | Best supported automated route if Bounced Studio or its distributor can grant the required provider access. |
| Apple Music for Artists | Private artist performance, geography and trends; downloadable CSV | Requires access to the relevant artist account. Reviewed support docs describe CSV exports; they do not document a general third-party artist-analytics OAuth API. | CSV import is the clearest documented artist-access fallback; an importer is not implemented by this patch. |

Apple documents [developer-token authentication](https://developer.apple.com/documentation/AppleMusicAPI/generating-developer-tokens)
and the [$99 annual membership](https://developer.apple.com/programs/whats-included/).
Direct REST would remove the scraper intermediary for overlapping catalog fields;
better latency/reliability is an expectation, not a benchmark measured here. It
may not have field-for-field parity with Omkar's additional public-page metadata.

The separate [Music Analytics API](https://itunespartner.apple.com/music/support/5241-music-analytics-api)
returns aggregated listener/play counts and has privacy thresholds/rounding for
demographic queries. Apple's [July 2026 announcement](https://itunespartner.apple.com/5603-music-analytics-api-access-audience-segmentation)
identifies its engagement/overlap access as available to label partners. The
[Partner Program](https://musicpartners.apple.com/) is another contact-based route,
not a published $99 developer entitlement. The similarly named [Music Artist API](https://itunespartner.apple.com/music/assets/music-artist-api.pdf)
manages artist metadata/assets and should not be confused with analytics.

## Connecting actual artist analytics

First establish whether the customer has iTunes Connect **Music provider/label**
access or only Apple Music for Artists. Provider access needs an appropriately
scoped key and permission to the relevant catalog. Do not create a pretend
"Connect Apple" button using MusicKit listener authorization: that does not grant
these analytics permissions.

With artist-only access, Apple's documented [analytics export workflow](https://artists.apple.com/support/1105-understand-your-analytics)
lets the user select a date range and download their catalog data as CSV. Apple
reports data in UTC and documents a delay of up to 48 hours. A future import should
retain original files, date ranges, source identity and reported metric definitions;
use reviewed stable song mappings and explicit artist grants; deduplicate repeat
imports; and keep interval plays separate from public cumulative counters. Do not
infer a catalog join from title/ISRC alone or sum overlapping report ranges.

Recommendation: retain Omkar for now, fix metadata enrichment independently, and
use provider API access if available; otherwise implement a scoped CSV import
once a real export's schema is available. A distributor-supplied analytics feed is
also worth assessing against that distributor's actual account/API capabilities.
Do not store Apple passwords or browser-session cookies in the cron.

## Metadata enrichment implemented locally

Authorized Spotify/Apple/SoundCloud song discovery now fills missing duration,
explicit status, release date/precision and language, records supported field
observations, adds source-linked **unverified** ISRCs and genres, and creates a
private cover-art URL reference with an explicit asset grant. Artwork URLs are
restricted to known provider CDNs. The reference records the source UUID; it does
not download a durable file or approve its rights/publication. Existing values,
manual clears, overrides, artwork choices, identifiers/genre decisions, removed
rows and access grants are preserved. Source identifiers never merge songs.

Song pages expose collapsible platform-reported metadata per source, including
album/label/copyright/composer/credits/audio information, with full source links.
These claims do not manufacture collaborator identities, legal rights ownership
or release containers. The Apple pulse panel explains why public data lacks plays.

A no-network backfill is available for already-saved receipts:

```sh
# Use the deployment's configured Django environment; default previews and rolls back.
python manage.py backfill_platform_catalog --artist ARTIST_UUID
# After reviewing that preview and following production backup/runbook requirements:
python manage.py backfill_platform_catalog --artist ARTIST_UUID --apply
```

The default limit is 2,000 latest track receipts (adjust with `--limit`). It shares
the collector's PostgreSQL advisory lock, rechecks enabled jobs/current verified
customer profiles, and reports created recordings, filled fields and added covers/
identifiers/genres. Discovery can create missing drafts as well as enrich existing
accepted/granted songs. No new provider request, metric observation or source
snapshot is created. A dry run rolls all writes back; apply remains atomic.


Verification: **493 PostgreSQL tests passed**, including import and access regressions.
Independent review found a staff manual-clear preservation gap; it was fixed with
Operations AuditEvent and Django admin history coverage. Django/migration, schema
and whitespace checks passed. Synthetic browser previews show artwork, imported
fields, expanded metadata and the Apple pulse message; all fixture writes were
rolled back and read back. Evidence is ignored under
`artifacts/apple-music-research/`. Changes remain uncommitted/unpushed on `main`;
no production backfill, scraper run, Apple connection or deployment was performed.
