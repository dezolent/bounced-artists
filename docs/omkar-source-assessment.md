# Omkar source-code/self-hosting assessment

Subsequently implemented for eight platforms; Deezer and Boomplay are excluded.
See the [self-hosted deployment walkthrough](self-hosted-scrapers.md). The findings
below retain the original ten-repository assessment as dated evidence.

Checked **2026-10-24** using the GitHub connector against all ten supplied
repositories. This assessment is based on source inspection, not successful live
scrapes. No upstream code was executed, no proxy purchased, no service provisioned,
and no production collector configuration changed.

## Answer

**Yes for nine of the ten linked repositories.** They contain implementations,
local HTTP servers, dependencies and Dockerfiles, with MIT licenses. The inspected
transports call the music/social platforms directly rather than the Omkar hosted
API, so running those implementations would remove the Omkar API-key/monthly-call
dependency for those requests. It would replace a managed API with infrastructure,
platform request limits, operational maintenance and potentially proxy charges.

**Deezer is the exception:** its supplied repository currently has just README,
SECURITY, `.gitignore` and a playground image. There is no runnable scraper,
Dockerfile, dependency file or LICENSE in that tree. The Boomplay repository has
an unrelated `deezer/parsers.py`, but no Deezer transport/service; that is not a
complete replacement either.

## Repository results

Each link below is pinned to the inspected revision. MIT notices must remain with
redistributed code. The software license does not establish rights to publish
lyrics, artwork or other returned content.

| Platform | Source revision | What was verified | Application relevance |
| --- | --- | --- | --- |
| Apple Music | [0a32f04ad209](https://github.com/omkarcloud/apple-music-scraper/tree/0a32f04ad2092dda915aa34afa29a66f57924b9b) | MIT; HTTP transport, Bottle/cheroot server, Python 3.12 slim Dockerfile | Replace the current public catalog scraper. Still no private artist play counts. |
| YouTube | [24c9aac64a34](https://github.com/omkarcloud/youtube-scraper/tree/24c9aac64a34822a2f45641bc5989d864bf6d845) | MIT; HTTP transport and channel/video endpoints | Existing channel/video metrics; upstream availability still needs a live test. |
| TikTok | [683959302488](https://github.com/omkarcloud/tiktok-scraper/tree/683959302488fd773b56e2cf737e591bf12aa4be) | MIT; HTTP transport/signing and user/video/music endpoints | Existing owned-video and artist-sound usage collection. Datacenter reliability is not established by source inspection. |
| Spotify | [8c8a1c81a583](https://github.com/omkarcloud/spotify-scraper/tree/8c8a1c81a583aef8710b832197eb2a7cc841d137) | MIT; HTTP transport, artist/album/track/stream-count endpoints | Existing public audience metrics, discography and catalog enrichment. Official Spotify API credentials remain a separate integration. |
| SoundCloud | [0ac4f507a4b5](https://github.com/omkarcloud/soundcloud-scraper/tree/0ac4f507a4b541f0770af27191fbe429b1a4236b) | MIT; HTTP transport and user/track endpoints | Existing uploaded-track and public engagement collection. |
| Instagram | [55c98ff55b1a](https://github.com/omkarcloud/instagram-scraper/tree/55c98ff55b1ab671ef95c77aa1cbfa50494b74ed) | MIT; HTTP transport and public profile/post/reel endpoints | Existing public profile/content statistics; no claim of private Insights access. |
| Shazam | [76dcc1bfffc8](https://github.com/omkarcloud/shazam-scraper/tree/76dcc1bfffc8a0df3c38ba945795b570f42a878e) | MIT; HTTP transport; song/artist/charts/popular-segment endpoints; Dockerfile adds ffmpeg for recognition | Useful discovery/chart/context data. Song details does not include a total-Shazams counter. |
| Musixmatch | [ef66119ac3f3](https://github.com/omkarcloud/musixmatch-scraper/tree/ef66119ac3f30503abab6151187748370e04e963) | MIT; HTTP transport; lyrics/writers/credits/translation endpoints | Candidate lyric text/credits where returned and unrestricted. Existing edited lyrics must remain intact. |
| Deezer | [26c2fac0e7bc](https://github.com/omkarcloud/deezer-scraper/tree/26c2fac0e7bc6fbb793626d241fc4e4ea9bc576f) | Documentation/image only; no implementation or license file | Needs a separate implementation/source or an independently assessed official API. |
| Boomplay | [59076716ebbd](https://github.com/omkarcloud/boomplay-scraper/tree/59076716ebbd5978cc5b817011f4015feef92e40) | MIT; HTTP plus browser fallback; Chrome-based Docker image and patchright dependencies | Song play/favorite/share/comment counters and artist/catalog data where returned. Higher operational complexity than the HTTP-only servers. |

Full root/module inventories, file hashes, inspected revisions and route sources
are retained in ignored `artifacts/omkar-source-assessment/repository-audit.json`.
The nine route modules were parsed with Python AST without executing them.

## Compatibility with Bounced Artists

The six current platforms' source servers expose the paths used by our collectors,
including artist/song details and paginated uploads/discographies. Their route
modules explicitly support hosted-style paths and response shaping. This is good
migration evidence, not proof that every live payload, error or cursor is identical.
A representative three-customer smoke run is still required before switching live.

Our `omkar_client` currently fixes the hosted origin and requires `OMKAR_API_KEY`.
A source deployment alone would not redirect the existing cron. The application
would need a trusted per-platform backend/origin selection, omit the Omkar key
for self-hosted calls, and retain the current fixed-path/redirect protections,
request/time budgets, retries, source snapshots and catalog/access guards. Do not
silently spend the hosted allowance as fallback after a self-hosted failure.
Transport provenance and same-day cache compatibility need to be explicit.

Keep the Django web app and PostgreSQL as they are. Isolate the upstream runtime
from Django: the repositories use Python 3.12 Dockerfiles, top-level `config` and
`routes` modules, and overlapping package names. Importing several directly into
our Django process would create dependency/module conflicts. Separate private
servers or isolated worker processes can preserve the existing HTTP collector
contract. These servers do not provide application API-key authentication in the
inspected routes; they should not become unauthenticated public endpoints.

For three customers, I would favor a bounded scheduled worker arrangement rather
than starting nine always-on services without first measuring resource use. A
browser-heavy Boomplay runtime should be isolated from the lighter transports.
No particular worker packaging or service topology was built in this assessment.

## Cost and remaining limits

The Omkar hosted request allowance would cease to apply to direct self-hosted
requests. Target-platform blocks/rate limits, retries and incomplete results still
exist; the upstream configs expose optional proxy settings, and some transports
are designed to use proxies under blocking. An Omkar request can fan out into
multiple platform requests, so one request is not necessarily one upstream fetch.
Self-hosting also does not fix an unsupported metric such as private Apple plays.

[Railway's current resource pricing](https://docs.railway.com/pricing/plans#resource-usage-pricing)
lists RAM at $10/GB-month, CPU at $20/vCPU-month and network egress at $0.05/GB
for standard usage, with plan inclusion/minimums handled separately. As a purely
illustrative calculation, a worker consuming 1 GB RAM and one full CPU for two
hours daily over 30 days uses about $2.50 in compute. This is **not a forecast**:
actual scrape duration, idle processes, browser usage, data transfer, proxies and
existing plan usage would determine the bill. Proxy prices were not evaluated.

## New-platform data boundaries

**Shazam:** the inspected `/songs/details` result merges song metadata and Shazam/
Apple identities, without total recognitions. `/songs/popular-segments` has
segment-level `shazam_count` values; these are not a documented lifetime total and
must not be summed/relabelled as one. Charts and popular segments may still be
useful for discovery and content planning. Existing accepted Apple song IDs are a
safer starting identity than name searches or the source's ambiguous ISRC resolver.

**Musixmatch:** `/tracks/lyrics` can return text, language, copyright, writer/
publisher claims, verification and restriction flags. The parser explicitly removes
full text for restricted responses. Preserve those restrictions and provenance;
keep partial snippets distinct from full lyrics. Appropriate customer-owned lyric
imports should be private draft/candidate assets and never overwrite edited text,
translations or timing. Public publication remains a separate reviewed action.
No lyrics were fetched or imported during this assessment.

**Deezer:** existing official-site evidence contains 27 Deezer track/album URLs for
Dezolent and six for SRY4U; none for Morbeato. Those are catalog links, not verified
artist profile IDs. A new adapter must resolve/confirm identities and distinguish
fans, ranks and playlist data from actual track plays. The supplied repository
cannot implement this by itself.

**Boomplay:** the parser includes song `play_count`, `favorite_count`, `share_count`
and `comment_count`, with song/artist/discography endpoints available. None of our
current official-site evidence contains Boomplay profiles. Verify each customer
identity before collection; a name match alone is insufficient.

The same existing customer boundary applies to every expansion: only Dezolent,
Morbeato and SRY4U. Collaborators/features never become scrape targets. No new
Shazam/Musixmatch/Boomplay profile links were found in the current official-site
manifest, and no new profile was approved by this assessment.

Recommendation: self-hosting is viable for the six existing providers; retain the
current collector/persistence safeguards and verify live payloads before migration.
Shazam and Musixmatch add useful but narrower capabilities than their names might
suggest. Assess Deezer separately and benchmark Boomplay's browser cost. This
research does not change the application, provision infrastructure or switch off
the current Omkar backend.
