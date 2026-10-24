"""Omkar endpoint contracts checked against public playground specs on 2026-10-22."""
from dataclasses import dataclass
import time
from urllib.parse import urlsplit

from django.utils import timezone

from .http import CollectionError, SpotifyClient, omkar_client, safe_error_code

PLATFORMS = {"spotify", "soundcloud", "apple-music", "youtube", "instagram"}
# Public list and detail schemas expose the same post fields (checked 2026-10-23).
# Only skip enrichment when metadata as well as counters is already present.
INSTAGRAM_POST_FIELDS = {
    "id", "code", "link", "type", "product_type", "caption", "published_at",
    "is_caption_edited", "image", "video", "width", "height", "accessibility_caption",
    "title", "stats", "are_likes_hidden", "are_comments_disabled", "is_paid_partnership",
    "is_pinned", "location", "audio", "tagged_users", "coauthors", "sponsors",
    "carousel", "carousel_count", "owner",
}


@dataclass
class Collected:
    endpoint: str
    kind: str
    external_id: str
    payload: dict
    incomplete: bool = False
    limited: bool = False


def identifier(value):
    result = str(value or "")
    if not result or len(result) > 255 or not all(c.isalnum() or c in "_-" for c in result):
        raise CollectionError("invalid_object_id")
    return result


def profile_locator(platform, url):
    parsed = urlsplit(url)
    allowed = {
        "spotify": {"open.spotify.com"}, "soundcloud": {"soundcloud.com", "www.soundcloud.com"},
        "apple-music": {"music.apple.com"}, "youtube": {"youtube.com", "www.youtube.com"},
        "instagram": {"instagram.com", "www.instagram.com"},
    }
    if platform not in allowed or parsed.scheme != "https" or parsed.hostname not in allowed[platform] or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise CollectionError("invalid_profile_url")
    parts = parsed.path.strip("/").split("/")
    if platform == "spotify":
        if len(parts) != 2 or parts[0] != "artist":
            raise CollectionError("invalid_artist_profile")
        return identifier(parts[1])
    if platform == "apple-music":
        if "artist" not in parts:
            raise CollectionError("invalid_artist_profile")
        return identifier(parts[-1])
    if platform == "youtube":
        if not ((len(parts) == 1 and parts[0] and parts[0] not in {"watch", "shorts", "playlist", "feed", "results", "embed", "live"}) or (len(parts) == 2 and parts[0] in {"channel", "c", "user"})):
            raise CollectionError("invalid_channel_profile")
        return "https://www.youtube.com/" + "/".join(parts)
    if len(parts) != 1 or not parts[0] or parts[0] in {"p", "reel", "explore", "discover", "sets"}:
        raise CollectionError("invalid_user_profile")
    return parts[0]


def pages(fetch, key, *, cursor=False, max_pages=1000, **params):
    """Reconstruct pagination on the fixed endpoint; never follow a returned URL."""
    seen = set()
    for page in range(1, max_pages + 1):
        for attempt in range(2):
            try:
                response = fetch(**params) if cursor else fetch(page=page, **params)
                break
            except CollectionError as exc:
                if attempt or safe_error_code(exc) not in {"http_429", "http_500", "http_502", "http_503", "http_504", "network_error"}:
                    raise
                # Retry this exact page once after the HTTP client's short retries.
                # Previously one exhausted 502 discarded the rest of this branch.
                time.sleep(5)
        items = response.get(key)
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise CollectionError("invalid_collection_shape")
        yield page, response, items
        if cursor:
            token = response.get("next_cursor")
            if not token:
                if response.get("has_more"):
                    raise CollectionError("missing_pagination_cursor")
                return
            if not isinstance(token, str) or token in seen:
                raise CollectionError("repeated_pagination_cursor")
            seen.add(token)
            params["cursor"] = token
        else:
            total = response.get("total_pages")
            if not response.get("next") and (total is None or page >= total):
                return
    raise CollectionError("pagination_limit")


def failure(endpoint, external_id, error):
    # Budget exhaustion is invocation-wide; an invalid credential cannot improve
    # by trying every item in the same provider account.
    code = safe_error_code(error)
    if code in {"request_limit", "job_request_limit", "missing_omkar_credentials", "invalid_request_path"}:
        raise error
    return Collected(endpoint + "-unavailable", "collection", external_id,
                     {"endpoint": endpoint, "error_code": code}, incomplete=True)


def profile_failure(endpoint, locator, error):
    # Verified artist IDs/usernames can still drive independent catalog calls.
    # Account denials and exhausted budgets must stop immediately.
    if safe_error_code(error) not in {
        "http_429", "http_500", "http_502", "http_503", "http_504", "network_error",
        "invalid_json", "invalid_response_shape", "response_too_large",
    }:
        raise error
    return failure(endpoint, locator, error)


def detail(fetch, path, endpoint, kind, external_id, **params):
    try:
        payload = fetch(path, **params)
    except CollectionError as exc:
        yield failure(endpoint, external_id, exc)
    else:
        yield Collected(endpoint, kind, external_id, payload)


def collection(fetch, path, key, locator, *, cursor=False, **params):
    endpoint = path.strip("/").replace("/", "-")
    try:
        for page, response, items in pages(lambda **p: fetch(path, **p), key, cursor=cursor, **params):
            yield Collected(endpoint, "collection", f"{locator}:{page}", response), items
    except CollectionError as exc:
        # This branch is incomplete, but independent collections can continue.
        yield failure(endpoint, locator, exc), []


def optional_spotify_collection(fetch, path, artist_id):
    endpoint = path.strip("/").replace("/", "-").replace("artists-", "artist-", 1)
    try:
        response = fetch(path, artist=artist_id)
        items = response.get("results")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise CollectionError("invalid_collection_shape")
    except CollectionError as exc:
        yield failure(endpoint, artist_id, exc), []
    else:
        # These two documented endpoints expose no page/cursor parameter. A
        # capped shelf is a provider coverage limit, not a recoverable failed run.
        yield Collected(endpoint, "collection", artist_id, response,
                        limited=(response.get("total_count") or 0) > len(items)), items


def collect_spotify(locator, omkar, spotify):
    artist_id = profile_locator("spotify", locator)
    try:
        payload = omkar("/artists/details", artist=artist_id)
    except CollectionError as exc:
        yield profile_failure("artist-details", artist_id, exc)
    else:
        yield Collected("artist-details", "artist", identifier(payload.get("id")), payload)
    for row, _ in optional_spotify_collection(omkar, "/artists/discovered-on", artist_id):
        yield row
    releases = []
    for row, items in collection(omkar, "/artists/albums", "results", artist_id, artist=artist_id, limit=100):
        yield row
        releases.extend(items)
    for row, items in optional_spotify_collection(omkar, "/artists/appears-on", artist_id):
        yield row
        releases.extend(items)
    albums_seen, tracks_seen = set(), set()
    official_error = ""
    for album in releases:
        try:
            album_id = identifier(album.get("id"))
        except CollectionError as exc:
            yield failure("album-details", artist_id, exc)
            continue
        if album_id in albums_seen:
            continue
        albums_seen.add(album_id)
        yield from detail(omkar, "/albums/details", "album-details", "album", album_id, album=album_id)
        for row, tracks in collection(omkar, "/albums/tracks", "results", album_id, album=album_id, limit=100):
            yield row
            for track in tracks:
                # An appearance may be a compilation containing other artists.
                if not any(isinstance(a, dict) and str(a.get("id")) == artist_id for a in (track.get("artists") or [])):
                    continue
                try:
                    track_id = identifier(track.get("id"))
                except CollectionError as exc:
                    yield failure("track-details", album_id, exc)
                    continue
                if track_id in tracks_seen:
                    continue
                tracks_seen.add(track_id)
                yield from detail(omkar, "/tracks/details", "track-details", "track", track_id, track=track_id)
                if not official_error:
                    try:
                        official = spotify("/tracks/" + track_id)
                    except CollectionError as exc:
                        code = safe_error_code(exc)
                        # Only an account-wide denial disables subsequent official
                        # calls; transient failures on one track stay local to it.
                        if code in {"http_401", "http_403", "missing_spotify_credentials", "invalid_spotify_token_response"}:
                            official_error = code
                        yield failure("spotify-api", track_id, exc)
                    else:
                        yield Collected("spotify-track", "track", track_id, official)
                else:
                    yield failure("spotify-api", track_id, CollectionError(official_error))


def collect_soundcloud(locator, fetch):
    user = profile_locator("soundcloud", locator)
    try:
        payload = fetch("/users/details", user=user)
    except CollectionError as exc:
        # All following provider calls use the verified username. Its fallback
        # locator identifies collection receipts only, never a platform user.
        user_id = user
        yield profile_failure("user-details", user, exc)
    else:
        user_id = identifier(payload.get("id"))
        yield Collected("user-details", "user", user_id, payload)
    seen = set()
    for row, tracks in collection(fetch, "/users/tracks", "tracks", user_id, cursor=True, user=user):
        yield row
        for track in tracks:
            try:
                track_id = identifier(track.get("id"))
            except CollectionError as exc:
                yield failure("track-details", user_id, exc)
                continue
            if track_id not in seen:
                seen.add(track_id)
                yield from detail(fetch, "/tracks/details", "track-details", "track", track_id, track=track_id)
    for row, _ in collection(fetch, "/users/playlists", "playlists", user_id, user=user):
        yield row
    for path in ("/users/spotlight", "/users/social-links"):
        yield from detail(fetch, path, path.strip("/").replace("/", "-"), "collection", user_id, user=user)


def collect_apple(locator, fetch):
    artist = profile_locator("apple-music", locator)
    country = urlsplit(locator).path.strip("/").split("/")[0]
    if len(country) != 2 or not country.isalpha():
        raise CollectionError("invalid_apple_storefront")
    original_fetch = fetch
    fetch = lambda path, **params: original_fetch(path, country=country.upper(), **params)
    profile_songs = []
    try:
        payload = fetch("/artists/details", artist=artist)
    except CollectionError as exc:
        yield profile_failure("artist-details", artist, exc)
    else:
        yield Collected("artist-details", "artist", artist, payload)
        profile_songs = payload.get("top_songs", [])
    albums_seen, songs_seen = set(), set()

    def collect_song(song, source_id):
        if song.get("type") not in (None, "song", "songs"):
            return
        try:
            song_id = identifier(song.get("id"))
        except CollectionError as exc:
            yield failure("song-details", source_id, exc)
            return
        if song_id in songs_seen:
            return
        songs_seen.add(song_id)
        try:
            payload = fetch("/songs/details", song=song_id)
        except CollectionError as exc:
            yield failure("song-details", song_id, exc)
            return
        song_artists = payload.get("artists")
        if not isinstance(song_artists, list) or not song_artists or any(not isinstance(a, dict) for a in song_artists):
            yield Collected("song-artist-unconfirmed", "collection", song_id, payload, incomplete=True)
        elif any(str(a.get("id")) == artist for a in song_artists):
            yield Collected("song-details", "track", song_id, payload)

    for row, albums in collection(fetch, "/artists/albums", "albums", artist, artist=artist, type="all", limit=100, max_pages=500):
        yield row
        for album in albums:
            try:
                album_id = identifier(album.get("id"))
            except CollectionError as exc:
                yield failure("album-details", artist, exc)
                continue
            if album_id in albums_seen:
                continue
            albums_seen.add(album_id)
            try:
                payload = fetch("/albums/details", album=album_id)
                tracks = payload.get("tracks")
                if not isinstance(tracks, list) or any(not isinstance(item, dict) for item in tracks):
                    raise CollectionError("missing_album_tracks")
            except CollectionError as exc:
                yield failure("album-details", album_id, exc)
                continue
            yield Collected("album-details", "album", album_id, payload, (payload.get("track_count") or 0) > len(tracks))
            for song in tracks:
                yield from collect_song(song, album_id)
    # Profile previews and the independent paginated top-songs endpoint can
    # recover songs omitted by failed album pages or incomplete track lists.
    if not isinstance(profile_songs, list) or any(not isinstance(song, dict) for song in profile_songs):
        yield failure("artist-top-songs", artist, CollectionError("invalid_collection_shape"))
    else:
        for song in profile_songs:
            yield from collect_song(song, artist)
    for row, songs in collection(fetch, "/artists/top-songs", "songs", artist, artist=artist, limit=100, max_pages=500):
        yield row
        for song in songs:
            yield from collect_song(song, artist)
    for path, key in (("/artists/music-videos", "music_videos"), ("/artists/playlists", "playlists")):
        for row, _ in collection(fetch, path, key, artist, artist=artist):
            yield row


def collect_youtube(locator, fetch):
    channel = profile_locator("youtube", locator)
    payload = fetch("/channels/details", channel=channel)
    channel_id = identifier(payload.get("channel", {}).get("id"))
    yield Collected("channel-details", "channel", channel_id, payload)
    seen = set()
    for path, key in (("/channels/videos", "videos"), ("/channels/shorts", "shorts"), ("/channels/live", "videos")):
        for row, videos in collection(fetch, path, key, channel_id, cursor=True, channel=channel_id):
            yield row
            for video in videos:
                try:
                    video_id = identifier(video.get("id"))
                except CollectionError as exc:
                    yield failure("video-details", channel_id, exc)
                    continue
                if video_id not in seen:
                    seen.add(video_id)
                    yield from detail(fetch, "/videos/details", "video-details", "video", video_id, video=video_id)
    for row, _ in collection(fetch, "/channels/playlists", "playlists", channel_id, cursor=True, channel=channel_id):
        yield row


def collect_instagram(locator, fetch):
    user = profile_locator("instagram", locator)
    profile = fetch("/users/profile", user=user)
    user_id = identifier(profile.get("id"))
    yield Collected("user-profile", "user", user_id, profile)
    if profile.get("is_private"):
        raise CollectionError("private_profile")
    seen = set()
    for path, key in (("/users/posts", "posts"), ("/users/reels", "reels")):
        for row, posts in collection(fetch, path, key, user_id, cursor=True, user=user):
            yield row
            for post in posts:
                try:
                    post_id = identifier(post.get("code"))
                    stable_id = identifier(post.get("id"))
                except CollectionError as exc:
                    yield failure("post-details", user_id, exc)
                    continue
                if post_id not in seen:
                    seen.add(post_id)
                    stats = post.get("stats")
                    if isinstance(stats, dict):
                        # The paginated endpoint already returns full public post
                        # counters. Persist them before any optional enrichment.
                        yield Collected("post-list-item", "post", stable_id,
                                        {**post, "_collection": {"endpoint": path.strip("/").replace("/", "-")}})
                    complete = (INSTAGRAM_POST_FIELDS <= post.keys() and isinstance(stats, dict) and
                                {"like_count", "comment_count", "play_count", "view_count", "repost_count"} <= stats.keys() and
                                isinstance(post.get("owner"), dict))
                    if not complete:
                        yield from detail(fetch, "/posts/details", "post-details", "post", stable_id, post=post_id)


# Only complete, stable item details are reusable. Profiles, discovery pages and
# albums containing possibly truncated track lists always refresh on retry.
REUSABLE_ENDPOINTS = {"track-details", "song-details", "video-details", "spotify-track", "post-details"}


class SnapshotCache(dict):
    def __init__(self):
        super().__init__()
        self.hits = 0
        self.day = timezone.now().date()


def cached_fetch(fetch, reuse, *, official=False):
    def get(path, **params):
        endpoint, external_id = None, None
        if official and path.startswith("/tracks/"):
            endpoint, external_id = "spotify-track", path.rsplit("/", 1)[-1]
        elif path in {"/tracks/details", "/songs/details", "/videos/details", "/posts/details"}:
            endpoint = path.strip("/").replace("/", "-").replace("tracks-", "track-").replace("songs-", "song-").replace("videos-", "video-").replace("posts-", "post-")
            external_id = str(params.get({"track-details": "track", "song-details": "song", "video-details": "video", "post-details": "post"}[endpoint]))
        if reuse.day == timezone.now().date() and (endpoint, external_id) in reuse:
            reuse.hits += 1
            return reuse[(endpoint, external_id)]
        return fetch(path, **params)
    return get


def collect(platform, locator, budget, *, reuse=None):
    fetch = omkar_client(platform, budget)
    if reuse is not None:
        fetch = cached_fetch(fetch, reuse)
    if platform == "spotify":
        spotify = SpotifyClient(budget).get
        if reuse is not None:
            spotify = cached_fetch(spotify, reuse, official=True)
        return collect_spotify(locator, fetch, spotify)
    return {"soundcloud": collect_soundcloud, "apple-music": collect_apple,
            "youtube": collect_youtube, "instagram": collect_instagram}[platform](locator, fetch)
