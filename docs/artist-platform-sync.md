# Daily artist platform collection

Implemented in the working tree on 2026-10-22. **Not deployed or scheduled in
Railway yet.** No production data, credentials, service configuration or public
content was changed. The user commits and pushes manually.

## What it collects

| Platform | Coverage |
| --- | --- |
| Spotify | Full paginated discography, appearance releases, discovered-on playlists, album metadata and track lists, Omkar track details and official Spotify track metadata. Artist stats include followers, monthly listeners, world rank and top-city listeners. Track details include plays, previews, artwork, release dates, copyright and contributor roles where provided. |
| SoundCloud | Profile, uploaded tracks with cursor pagination and individual details, playlists, spotlight and social links. Public plays, likes, reposts, comments and downloads where returned. |
| Apple Music | Profile, paginated albums, each album's songs and individual song details, music videos and artist playlists. Includes artwork, composer/artist credits, identifiers, label/copyright, dates and previews where returned. |
| YouTube | Channel, all paginated uploads, Shorts, live uploads, individual video metadata and channel playlists. Subscribers, views, likes and comments where exposed. Video details preserve chapters, music credits, subtitle availability and description links. |
| Instagram | Public profile, all paginated posts and Reels, individual post details. Followers, following, posts, likes, comments, views and plays where exposed. |

All returned metadata remains in private `SourceRecord` snapshots after credential
fields are removed. Images and preview URLs are references; this worker does not
download media. Comments, follower identities and private Instagram content are not
collected. No earnings, private Spotify for Artists data or unavailable counts are
invented. Omkar may omit fields or return rounded counts.

Spotify discography returns **album IDs**. The collector explicitly expands each
album through `/albums/tracks`, deduplicates actual track IDs, checks the artist
credit on compilation tracks, and then calls `/tracks/details` and Spotify
`/v1/tracks/{id}`. Albums and tracks remain different platform objects. Spotify API
access failures retain the remaining Omkar data and mark the run incomplete.
Official metadata is retained separately from scraper data.

## Artist access and review

Collection is explicitly restricted to the Bounced Studio customer workspaces
**Dezolent, Morbeato and SRY4U**, using immutable `bootstrap_key` identities rather
than editable names/slugs. Other artists remain collaborators/features and are
never collection targets, even if a profile is verified or an artist is accidentally
marked managed. Soft-removed profiles do not qualify.

The owner explicitly authorized the published links on `dezolent.com`,
`morbeato.com` and `sry4u.com` as verified sources on 2026-10-22.
`artists/data/official_workspace_links.json` contains the inspected public URLs and
source page hashes. Root-page artist links and the owning MusicGroup's `sameAs`
identify each customer's own profiles; collaborator/person schema is excluded.
`verify_official_artist_links` previews the resulting changes, and `--apply` adds
missing customer profile links, verifies matching existing profiles, and accepts
existing assigned song/release destinations present on that customer's site.
It never creates collaborator workspaces, matches songs by title/ISRC, approves
artwork/lyrics for publication or changes canonical editorial metadata.

Both web pre-deploy and cron startup apply this offline assertion once per evidence
version and record source/audit receipts. A later staff rejection/removal survives
repeat deployment and cron setup; a replaced profile URL is not silently restored.
Unrelated invalid existing URLs are skipped and counted, while the checked website
evidence remains strictly validated. Other new links still need staff verification.
Only verified supported HTTPS profiles on these three active managed workspaces
qualify for collection. Production profile verification has not been applied from
this checkout yet.

`configure_artist_sync` is idempotent. It preserves operator-paused jobs and
connection status, disables obsolete jobs, and never verifies links or changes
artist identity. A subsequently reverified profile's disabled job needs an explicit
operator re-enable. URL aliases for the same artist/platform identity select one
job. Each run rechecks the actual profile, customer identity and organization.

Artists, including viewers, open **Insights** in their private workspace. They can
search songs/releases, filter by platform, inspect metadata and external links,
view current audience observations and 30-day daily history, and see collection
freshness/failure status. Release details link to a filter that uses accepted
release/recording platform matches and explicitly assigned catalog tracks. Unmatched
platform discoveries are still visible in Insights and generate a staff review
item. They never auto-merge by name/ISRC, grant access, overwrite editorial fields,
or enter public projections. Metrics remain `pending` public-source observations
until reviewed; a missing or rejected value is not displayed as zero.

## Commands

No-network/no-write local previews:

```sh
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py configure_artist_sync
DJANGO_SETTINGS_MODULE=config.settings.local .venv/bin/python manage.py sync_artist_platforms
```

Apply only in the intended environment with credentials in process environment:

```sh
python manage.py configure_artist_sync --apply
python manage.py sync_artist_platforms --apply --request-limit 1000 --job-limit 250
```

To preview/apply the inspected official website assertions independently:

```sh
python manage.py verify_official_artist_links
python manage.py verify_official_artist_links --apply
```

`--artist <UUID>` scopes a sync invocation; `--force` allows a repeat of successful
jobs. Consecutive source snapshots with the same payload, endpoint, object and job
on the same UTC day are reused. Changed same-day metrics create revisions, including
a value returning to an earlier value (A → B → A). A new UTC day keeps
another dated snapshot even if the values are unchanged. Failed runs preserve
completed source snapshots; each snapshot and its metrics/review entry are atomic.

One PostgreSQL advisory lock prevents overlapping collectors. A killed container
releases its lock; the next invocation marks abandoned running receipts interrupted.
Success makes a job due again at the next UTC midnight. Failures stay due. The
Railway process exits nonzero when any job fails and never sleeps indefinitely.
Provider truncation, missing/repeated cursors, a page cap or the HTTP request budget
mark a run incomplete/failed rather than claiming a full catalog. Non-paginated
Omkar appears-on/discovered-on results cannot be expanded beyond provider coverage.

## Railway setup after manual push

Use the existing [project and production environment](operations.md#railway-resources).
Keep Django web and PostgreSQL intact. Add one separate service named
`bounced-artists-daily-sync` from the same GitHub repository, branch `main`:

1. Set the service's Railway config-file path to `/deploy/railway-artist-sync.toml`.
   It selects the existing Dockerfile, `sh scripts/sync_artist_platforms.sh`, daily
   `0 9 * * *` (09:00 UTC = 05:00 Eastern daylight / 04:00 Eastern standard time),
   one replica, and restart policy `NEVER`. No public domain, volume, healthcheck,
   migration/pre-deploy command or web start command is needed for this service.
2. Set `DJANGO_SETTINGS_MODULE=config.settings.production` and
   `DATABASE_URL=${{bounced_artists.DATABASE_URL}}`. Reference the web service's
   existing `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS` using Railway variable references; do not copy or
   reveal values. R2 variables are unnecessary for this metadata collector.
3. Attach the existing shared variables by reference:
   `OMKAR_API_KEY=${{shared.OMKAR_API_KEY}}`,
   `SPOTIFY_CLIENT_ID=${{shared.SPOTIFY_CLIENT_ID}}`, and
   `SPOTIFY_CLIENT_SECRET=${{shared.SPOTIFY_CLIENT_SECRET}}`.
   Shared variables must be referenced on the service to enter its environment.
4. Optionally set `ARTIST_SYNC_REQUEST_LIMIT` (default 1000 total HTTP attempts per
   invocation) and `ARTIST_SYNC_JOB_LIMIT` (default 250 jobs). Retries and Spotify
   OAuth calls count toward the HTTP limit. Request budgets cap work; they are not
   a subscription or cost guarantee. Size the limits to the catalog and Omkar plan.
5. Deploy the committed web code first, then deploy/run the cron service once.
   There are no schema changes in this feature. Verify the command summary, private
   SyncRun receipts, source/metric counts, and the authenticated artist Insights UI.
   Confirm the next scheduled execution in Railway. The offline website verifier
   prepares the three customers' supported profiles automatically. Rejected,
   removed or unsupported profiles produce no API calls.

Pause a job with `enabled=False` in Operations. To pause all collection, remove the
cron schedule or stop the separate cron service. Do not modify the web start command
or database. Daily snapshots grow the existing database; retention/archival is a
separate explicit operation and this worker never deletes history.

Railway configuration access recovered on 2026-10-22; the existing production web
service still follows `main`. The new uncommitted collector code and website-link
verification have not been deployed or applied in production. No credential values
were read. Configuration access alone does not establish production health.

## API references

Public Omkar playground specs (hosts, `API-Key` header, paths and pagination)
were inspected on 2026-10-22. Network tests below use synthetic responses; live
credentialed calls have not been made.

- [Spotify artist discography](https://www.omkar.cloud/tools/spotify-scraper/artist-discography),
  [album tracks](https://www.omkar.cloud/tools/spotify-scraper/album-tracks),
  [track details](https://www.omkar.cloud/tools/spotify-scraper/track-details)
- [Spotify Web API Get Track](https://developer.spotify.com/documentation/web-api/reference/get-track)
- [SoundCloud playground](https://www.omkar.cloud/tools/soundcloud-scraper/playground)
- [Apple Music playground](https://www.omkar.cloud/tools/apple-music-scraper/playground)
- [YouTube playground](https://www.omkar.cloud/tools/youtube-scraper/playground)
- [Instagram playground](https://www.omkar.cloud/tools/instagram-scraper/playground)
- [Railway cron jobs](https://docs.railway.com/guides/cron-jobs)
- [Railway incident](https://status.railway.com/incident/QH8L8B3E)
