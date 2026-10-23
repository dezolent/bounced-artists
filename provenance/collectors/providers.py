"""Omkar endpoint contracts checked against public playground specs on 2026-10-22."""
from dataclasses import dataclass
from urllib.parse import urlsplit

from .http import CollectionError, SpotifyClient, omkar_client

PLATFORMS = {"spotify", "soundcloud", "apple-music", "youtube", "instagram"}


@dataclass
class Collected:
    endpoint: str
    kind: str
    external_id: str
    payload: dict
    incomplete: bool = False


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
        response = fetch(**params) if cursor else fetch(page=page, **params)
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


def collection(fetch, path, key, locator, *, cursor=False, **params):
    for page, response, items in pages(lambda **p: fetch(path, **p), key, cursor=cursor, **params):
        yield Collected(path.strip("/").replace("/", "-"), "collection", f"{locator}:{page}", response), items


def collect_spotify(locator, omkar, spotify):
    artist_id = profile_locator("spotify", locator)
    payload = omkar("/artists/details", artist=artist_id)
    yield Collected("artist-details", "artist", identifier(payload.get("id")), payload)
    discovered = omkar("/artists/discovered-on", artist=artist_id)
    yield Collected("artist-discovered-on", "collection", artist_id, discovered,
                    (discovered.get("total_count") or 0) > len(discovered.get("results", [])))
    releases = []
    for row, items in collection(omkar, "/artists/albums", "results", artist_id, artist=artist_id, limit=100):
        yield row
        releases.extend(items)
    appears = omkar("/artists/appears-on", artist=artist_id)
    yield Collected("artist-appears-on", "collection", artist_id, appears,
                    (appears.get("total_count") or 0) > len(appears.get("results", [])))
    releases.extend(appears.get("results", []))
    albums_seen, tracks_seen = set(), set()
    official_error = ""
    for album in releases:
        album_id = identifier(album.get("id"))
        if album_id in albums_seen:
            continue
        albums_seen.add(album_id)
        yield Collected("album-details", "album", album_id, omkar("/albums/details", album=album_id))
        for row, tracks in collection(omkar, "/albums/tracks", "results", album_id, album=album_id, limit=100):
            yield row
            for track in tracks:
                # An appearance may be a compilation containing other artists.
                if not any(str(a.get("id")) == artist_id for a in (track.get("artists") or [])):
                    continue
                track_id = identifier(track.get("id"))
                if track_id in tracks_seen:
                    continue
                tracks_seen.add(track_id)
                # Omkar remains available even if the official API denies this app.
                yield Collected("track-details", "track", track_id, omkar("/tracks/details", track=track_id))
                if not official_error:
                    try:
                        official = spotify("/tracks/" + track_id)
                    except CollectionError as exc:
                        if str(exc) == "request_limit":
                            raise
                        official_error = str(exc)
                    else:
                        yield Collected("spotify-track", "track", track_id, official)
                if official_error:
                    yield Collected("spotify-api-unavailable", "collection", track_id,
                                    {"error_code": official_error}, incomplete=True)


def collect_soundcloud(locator, fetch):
    user = profile_locator("soundcloud", locator)
    payload = fetch("/users/details", user=user)
    user_id = identifier(payload.get("id"))
    yield Collected("user-details", "user", user_id, payload)
    seen = set()
    for row, tracks in collection(fetch, "/users/tracks", "tracks", user_id, cursor=True, user=user):
        yield row
        for track in tracks:
            track_id = identifier(track.get("id"))
            if track_id not in seen:
                seen.add(track_id)
                yield Collected("track-details", "track", track_id, fetch("/tracks/details", track=track_id))
    for path in ("/users/playlists",):
        for row, _ in collection(fetch, path, "playlists", user_id, user=user):
            yield row
    for path in ("/users/spotlight", "/users/social-links"):
        yield Collected(path.strip("/").replace("/", "-"), "collection", user_id, fetch(path, user=user))


def collect_apple(locator, fetch):
    artist = profile_locator("apple-music", locator)
    # Preserve the storefront of the verified profile rather than silently using US.
    country = urlsplit(locator).path.strip("/").split("/")[0]
    if len(country) != 2 or not country.isalpha():
        raise CollectionError("invalid_apple_storefront")
    original_fetch = fetch
    fetch = lambda path, **params: original_fetch(path, country=country.lower(), **params)
    yield Collected("artist-details", "artist", artist, fetch("/artists/details", artist=artist))
    albums_seen, songs_seen = set(), set()
    for row, albums in collection(fetch, "/artists/albums", "albums", artist, artist=artist, type="all"):
        yield row
        for album in albums:
            album_id = identifier(album.get("id"))
            if album_id in albums_seen:
                continue
            albums_seen.add(album_id)
            detail = fetch("/albums/details", album=album_id)
            tracks = detail.get("tracks")
            if not isinstance(tracks, list):
                raise CollectionError("missing_album_tracks")
            yield Collected("album-details", "album", album_id, detail, (detail.get("track_count") or 0) > len(tracks))
            for song in tracks:
                if song.get("type") not in (None, "song", "songs"):
                    continue
                song_id = identifier(song.get("id"))
                if song_id not in songs_seen:
                    songs_seen.add(song_id)
                    detail = fetch("/songs/details", song=song_id)
                    song_artists = detail.get("artists") or []
                    if any(str(a.get("id")) == artist for a in song_artists):
                        yield Collected("song-details", "track", song_id, detail)
                    elif not song_artists:
                        yield Collected("song-artist-unconfirmed", "collection", song_id, detail, incomplete=True)
    for row, _ in collection(fetch, "/artists/music-videos", "music_videos", artist, artist=artist):
        yield row
    for row, _ in collection(fetch, "/artists/playlists", "playlists", artist, artist=artist):
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
                video_id = identifier(video.get("id"))
                if video_id not in seen:
                    seen.add(video_id)
                    yield Collected("video-details", "video", video_id, fetch("/videos/details", video=video_id))
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
                post_id = identifier(post.get("code"))
                if post_id not in seen:
                    seen.add(post_id)
                    yield Collected("post-details", "post", identifier(post.get("id")), fetch("/posts/details", post=post_id))


def collect(platform, locator, budget):
    fetch = omkar_client(platform, budget)
    if platform == "spotify":
        return collect_spotify(locator, fetch, SpotifyClient(budget).get)
    return {"soundcloud": collect_soundcloud, "apple-music": collect_apple,
            "youtube": collect_youtube, "instagram": collect_instagram}[platform](locator, fetch)
