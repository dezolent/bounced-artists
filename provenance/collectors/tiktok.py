"""Collect only a verified public TikTok identity and its confirmed music usage.

Endpoint schemas were checked against the public playground contract, 2026-10-23.
Pagination always reconstructs a fixed request path; returned links are evidence.
"""
from .http import CollectionError
from .providers import Collected, failure, identifier, pages, profile_locator


TIKTOK_VIDEO_FIELDS = {
    "id", "link", "type", "description", "title", "language", "created_at",
    "location_created", "is_ad", "is_pinned", "is_private", "is_ai_generated",
    "is_original", "is_official", "is_duet_enabled", "is_stitch_enabled",
    "is_share_enabled", "playlist_id", "stats", "author", "author_stats", "music",
    "video", "images", "cover", "hashtags", "mentions", "location", "effects",
    "text_stickers", "anchors", "suggested_searches",
}
TIKTOK_VIDEO_STATS = {
    "play_count", "like_count", "comment_count", "share_count", "save_count", "repost_count",
}
TIKTOK_AUTHOR_FIELDS = {
    "id", "sec_uid", "username", "nickname", "link", "profile_picture",
    "is_verified", "is_private", "biography",
}
TIKTOK_AUTHOR_STATS = {
    "follower_count", "following_count", "like_count", "video_count",
    "friend_count", "liked_video_count",
}
TIKTOK_MUSIC_FIELDS = {
    "id", "title", "link", "author_name", "album", "duration_seconds", "play_link",
    "cover", "is_original_sound", "is_copyrighted", "is_commercial_use_allowed",
    "is_unlimited_music", "is_private", "streaming_links",
}
TIKTOK_MEDIA_FIELDS = {
    "duration_seconds", "width", "height", "quality", "format", "codec", "bitrate",
    "size_bytes", "cover", "origin_cover", "animated_cover", "play_link",
    "download_link", "qualities", "subtitles",
}


def _username(value):
    return value.removeprefix("@").casefold() if isinstance(value, str) else ""


def _matches_identity(value, profile):
    """An explicitly contradictory identifier must never establish ownership."""
    if not isinstance(value, dict):
        return False
    checks = []
    if value.get("id") not in (None, ""):
        checks.append(str(value["id"]) == str(profile["id"]))
    if value.get("username") not in (None, ""):
        checks.append(_username(value["username"]) == _username(profile["username"]))
    return bool(checks) and all(checks)


def _private(payload):
    return payload.get("is_private") not in (False, None)


def _foreign_author(value, profile):
    if not isinstance(value, dict):
        return False
    checks = []
    if value.get("id") not in (None, ""):
        checks.append(str(value["id"]) != str(profile["id"]))
    if value.get("username") not in (None, ""):
        checks.append(_username(value["username"]) != _username(profile["username"]))
    return bool(checks) and all(checks)


def _public_video(payload):
    author = payload.get("author")
    return not _private(payload) and not (isinstance(author, dict) and _private(author))


def _complete_video(payload):
    stats = payload.get("stats")
    if not (TIKTOK_VIDEO_FIELDS <= payload.keys() and isinstance(stats, dict)
            and TIKTOK_VIDEO_STATS <= stats.keys() and isinstance(payload.get("author"), dict)):
        return False
    for field, keys in (("author", TIKTOK_AUTHOR_FIELDS), ("author_stats", TIKTOK_AUTHOR_STATS),
                        ("music", TIKTOK_MUSIC_FIELDS), ("video", TIKTOK_MEDIA_FIELDS)):
        value = payload[field]
        if value is not None and not (isinstance(value, dict) and keys <= value.keys()):
            return False
    return True


def _receipt(fetch, path, key, locator, *, identity=None, response_id=None, **params):
    """Keep TikTok receipts distinct from other providers sharing these paths."""
    endpoint = "tiktok-" + path.strip("/").replace("/", "-")
    try:
        for page, response, items in pages(lambda **p: fetch(path, **p), key, cursor=True, **params):
            if identity is not None and "user" in response and not _matches_identity(response["user"], identity):
                raise CollectionError("profile_identity_mismatch")
            if response_id is not None:
                field, expected = response_id
                if str(response.get(field)) != expected:
                    raise CollectionError("music_identity_mismatch" if field == "music_id" else "playlist_identity_mismatch")
            yield Collected(endpoint, "collection", f"{locator}:{page}", response), items
    except CollectionError as exc:
        yield failure(endpoint, locator, exc), []


def _stats_with_fallback(payload, previous):
    """A successful sparse detail response must not erase valid list counters."""
    previous_stats = previous.get("stats")
    if not isinstance(previous_stats, dict):
        return payload
    stats = payload.get("stats")
    stats = dict(stats) if isinstance(stats, dict) else {}
    for key, value in previous_stats.items():
        if stats.get(key) is None and isinstance(value, (int, float)) and not isinstance(value, bool):
            stats[key] = value
    return {**payload, "stats": stats}


def collect_tiktok(locator, fetch, *, music_ids=(), is_artist_music=None):
    """Yield raw own-content evidence and separately scoped confirmed music usage.

    ``music_ids`` are previously accepted artist catalog IDs. ``is_artist_music``
    may provide persisted catalog ownership evidence, and must return exactly True.
    Titles, sound names and credited display names never establish ownership.
    """
    user = _username(profile_locator("tiktok", locator))
    profile = fetch("/users/profile", user=user)
    user_id = identifier(profile.get("id"))
    if _username(profile.get("username")) != user:
        raise CollectionError("profile_identity_mismatch")
    if not isinstance(profile.get("is_private"), bool):
        raise CollectionError("missing_privacy_status")
    if profile["is_private"]:
        raise CollectionError("private_profile")
    yield Collected("tiktok-user", "user", user_id, profile)

    music = {}
    accepted = set()
    for raw_id in music_ids:
        try:
            music_id = identifier(raw_id)
        except CollectionError as exc:
            yield failure("tiktok-music", user_id, exc)
            continue
        accepted.add(music_id)
        music[music_id] = None
    seen_videos = set()

    def discover_music(payload):
        sound = payload.get("music")
        if isinstance(sound, dict) and sound.get("id") not in (None, ""):
            music.setdefault(identifier(sound["id"]), None)

    def own_video(payload, endpoint):
        if not _public_video(payload) or not _matches_identity(payload.get("author"), profile):
            return
        try:
            video_id = identifier(payload.get("id"))
            if video_id in seen_videos:
                return
            seen_videos.add(video_id)
            discover_music(payload)
        except CollectionError as exc:
            yield failure("tiktok-video", user_id, exc)
            return
        collected = {**payload, "_collection": {"endpoint": endpoint, "scope": "artist_content"}}
        # Preserve counters before optional enrichment, including genuine zeroes.
        if isinstance(payload.get("stats"), dict):
            yield Collected("tiktok-video", "video", video_id, collected)
        if _complete_video(payload):
            return
        try:
            enriched = fetch("/videos/details", video=video_id)
            if str(enriched.get("id")) != video_id or not _matches_identity(enriched.get("author"), profile):
                raise CollectionError("video_identity_mismatch")
            if not _public_video(enriched):
                raise CollectionError("private_profile")
            discover_music(enriched)
        except CollectionError as exc:
            yield failure("tiktok-video", video_id, exc)
        else:
            yield Collected("tiktok-video", "video", video_id,
                            {**_stats_with_fallback(enriched, payload),
                             "_collection": {"endpoint": "tiktok-videos-details", "scope": "artist_content"}})

    for row, videos in _receipt(fetch, "/users/videos", "videos", user_id,
                                identity=profile, user=user, sort="latest"):
        yield row
        for payload in videos:
            yield from own_video(payload, row.endpoint)

    seen_playlists = set()
    for row, playlists in _receipt(fetch, "/users/playlists", "playlists", user_id,
                                   identity=profile, user=user):
        yield row
        for playlist in playlists:
            try:
                playlist_id = identifier(playlist.get("id"))
                if playlist_id in seen_playlists:
                    continue
                seen_playlists.add(playlist_id)
                if not _matches_identity(playlist.get("creator"), profile):
                    raise CollectionError("playlist_identity_mismatch")
                metadata = fetch("/playlists/details", playlist=playlist_id)
                if str(metadata.get("id")) != playlist_id or not _matches_identity(metadata.get("creator"), profile):
                    raise CollectionError("playlist_identity_mismatch")
            except CollectionError as exc:
                yield failure("tiktok-playlists-details", str(playlist.get("id") or user_id), exc)
                continue
            yield Collected("tiktok-playlists-details", "collection", playlist_id, metadata)
            for video_row, videos in _receipt(fetch, "/playlists/videos", "videos", playlist_id,
                                              response_id=("playlist_id", playlist_id), playlist=playlist_id):
                yield video_row
                for payload in videos:
                    yield from own_video(payload, video_row.endpoint)

    for music_id in music:
        try:
            payload = fetch("/music/details", music=music_id)
            if str(payload.get("id")) != music_id:
                raise CollectionError("music_identity_mismatch")
        except CollectionError as exc:
            yield failure("tiktok-music", music_id, exc)
            continue
        if _private(payload):
            yield failure("tiktok-music", music_id, CollectionError("private_profile"))
            continue
        owned = (music_id in accepted or _matches_identity(payload.get("author"), profile)
                 or (is_artist_music is not None and is_artist_music(payload) is True))
        if not owned:
            yield Collected("tiktok-music-unconfirmed", "collection", music_id,
                            {**payload, "_collection": {"scope": "unconfirmed_music"}})
            continue
        yield Collected("tiktok-music", "music", music_id,
                        {**payload, "_collection": {"scope": "artist_music"}})
        seen_usage = set()
        for row, videos in _receipt(fetch, "/music/videos", "videos", music_id,
                                    response_id=("music_id", music_id), music=music_id):
            yield row
            for video in videos:
                try:
                    sound = video.get("music")
                    if isinstance(sound, dict) and sound.get("id") not in (None, "") and str(sound["id"]) != music_id:
                        raise CollectionError("music_identity_mismatch")
                    video_id = identifier(video.get("id"))
                    if video_id in seen_usage or video_id in seen_videos or not _foreign_author(video.get("author"), profile):
                        continue
                    if not _public_video(video):
                        continue
                    seen_usage.add(video_id)
                except CollectionError as exc:
                    yield failure("tiktok-usage-video", music_id, exc)
                    continue
                yield Collected("tiktok-usage-video", "usage_video", video_id,
                                {**video, "_collection": {"music_id": music_id, "scope": "music_usage"}})
