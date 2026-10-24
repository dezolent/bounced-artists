"""Join TikTok sounds to existing catalog songs using accepted stable identities.

This module never creates recordings or changes existing match decisions. The
provider may collect an owned original sound without obtaining a catalog join.
"""
import re
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from artists.customer_workspaces import collection_artists
from artists.models import ArtistProfileLink, ArtistResourceGrant
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.querysets import active_rows
from portal.recording_metrics import canonical_url, locator
from portal.validation import safe_external_url
from provenance.models import SyncJob


STREAMING_PLATFORMS = {"spotify": "spotify", "apple_music": "apple-music",
    "apple-music": "apple-music", "soundcloud": "soundcloud"}
MUSIC_KINDS = {"music", "track", "recording"}


def _id(value, *, numeric=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value)
    pattern = r"[0-9]{1,255}" if numeric else r"[A-Za-z0-9_-]{1,255}"
    return value if re.fullmatch(pattern, value) else ""


def _music_url_id(url):
    try:
        parsed = urlsplit(safe_external_url(url) or "")
    except (ValidationError, ValueError):
        return ""
    if parsed.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return ""
    match = re.fullmatch(r"/music/(?:[^/]+-)?([0-9]{1,255})/?", parsed.path)
    return match.group(1) if match else ""


def _music_id(obj):
    if obj.service.slug != "tiktok" or obj.object_type not in MUSIC_KINDS:
        return ""
    explicit = _id(obj.external_id, numeric=True)
    linked = _music_url_id(obj.url)
    if explicit and linked and explicit != linked:
        return ""
    # A track-shaped legacy object must actually represent a sound, not a video.
    if obj.url and not linked:
        return ""
    return explicit or linked


def _current_job(job):
    """Re-read authorization so stale caller objects cannot revive a disabled job."""
    try:
        job = active_rows(SyncJob.objects.filter(pk=job.pk, enabled=True,
            job_type="daily_public_platform")).select_related("connection__artist", "connection__service").first()
    except (ValidationError, ValueError, TypeError):
        return None
    if job is None:
        return None
    conn = job.connection
    if conn.status != "active" or not conn.artist_id or conn.service.slug != "tiktok" or conn.organization_id != conn.artist.organization_id:
        return None
    if not active_rows(collection_artists()).filter(pk=conn.artist_id).exists():
        return None
    if not active_rows(type(conn).objects.filter(pk=conn.pk)).exists():
        return None
    try:
        profile_id = job.cursor_json.get("profile_link_id")
        profile = active_rows(ArtistProfileLink.objects.filter(pk=profile_id, artist=conn.artist,
            platform=conn.service, verification_status="verified")).first()
        parsed = urlsplit(safe_external_url(profile.url) or "") if profile else None
    except (ValidationError, ValueError, TypeError, AttributeError):
        return None
    if parsed is None or parsed.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"} or not re.fullmatch(r"/@[A-Za-z0-9._]+/?", parsed.path):
        return None
    return job


def _matches(artist):
    grants = active_rows(ArtistResourceGrant.objects.filter(artist=artist))
    recordings = active_rows(Recording.objects.filter(organization=artist.organization,
        archived_at__isnull=True, pk__in=grants.values("recording_id")).exclude(status="archived"))
    objects = active_rows(PlatformObject.objects.all())
    return active_rows(RecordingPlatformMatch.objects.filter(recording__in=recordings,
        platform_object__in=objects, review_status="accepted")).select_related("platform_object__service")


def _track_identity(obj):
    """Reuse catalog URL decoding, including Apple's short /song/ID contract URL."""
    slug, kind, external_id, url = locator(obj)
    if slug == "apple-music" and url:
        parts = urlsplit(url).path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "song":
            kind, external_id = "track", _id(parts[1], numeric=True)
    return slug, kind, external_id, url


def _index(artist):
    identities, known_music = {}, {}
    for match in _matches(artist):
        obj = match.platform_object
        music_id = _music_id(obj)
        if music_id:
            known_music.setdefault(music_id, set()).add(match.recording_id)
        slug, kind, external_id, url = _track_identity(obj)
        if slug not in STREAMING_PLATFORMS.values() or kind != "track":
            continue
        if external_id:
            identities.setdefault((slug, "id", external_id), set()).add(match.recording_id)
        if url and slug == "soundcloud":
            identities.setdefault((slug, "url", url), set()).add(match.recording_id)
    return identities, {music_id for music_id, recording_ids in known_music.items() if len(recording_ids) == 1}


def _streaming_identity(entry):
    """Return declared id + decoded URL id, rejecting conflicting or unsafe claims."""
    platform = entry.get("platform")
    if not isinstance(platform, str):
        return False
    slug = STREAMING_PLATFORMS.get(platform)
    if slug is None:
        return None
    declared = _id(entry.get("song_id"), numeric=slug in {"apple-music", "soundcloud"})
    if entry.get("song_id") is not None and not declared:
        return False
    raw_url = entry.get("link")
    url_id, url = "", ""
    if raw_url:
        url = canonical_url(raw_url)
        if not url:
            return False
        parsed = urlsplit(raw_url)
        parts = parsed.path.strip("/").split("/")
        if slug == "spotify":
            if parsed.hostname != "open.spotify.com" or len(parts) not in {2, 3} or parts[-2] != "track":
                return False
            url_id = _id(parts[-1])
        elif slug == "apple-music":
            if parsed.hostname != "music.apple.com":
                return False
            if (len(parts) == 2 and parts[0] == "song") or (len(parts) >= 3 and parts[1] == "song"):
                url_id = _id(parts[-1], numeric=True)
            elif "album" in parts:
                songs = parse_qs(parsed.query).get("i", [])
                url_id = _id(songs[0], numeric=True) if len(songs) == 1 else ""
            if not url_id:
                return False
        elif parsed.hostname not in {"soundcloud.com", "www.soundcloud.com"} or len(parts) != 2 or parts[1] == "sets":
            return False
        elif parts[0] == "tracks":
            url_id = _id(parts[1], numeric=True)
        if slug != "soundcloud" and not url_id:
            return False
        if declared and url_id and declared != url_id:
            return False
    if not declared and not url_id and not (slug == "soundcloud" and url):
        return False
    return slug, declared or url_id, url if slug == "soundcloud" else ""


def _target(payload, identities):
    if not isinstance(payload, dict) or not isinstance(payload.get("streaming_links"), list):
        return None
    recordings = set()
    for entry in payload["streaming_links"]:
        if not isinstance(entry, dict):
            return None
        identity = _streaming_identity(entry)
        if identity is False:
            return None
        if identity is None:
            continue
        slug, external_id, url = identity
        ids = identities.get((slug, "id", external_id), set()) if external_id else set()
        urls = identities.get((slug, "url", url), set()) if url else set()
        # A SoundCloud permalink does not encode its numeric id. Both claims must
        # resolve to the same accepted song when the provider supplies both.
        if slug == "soundcloud" and external_id and url and ids != urls:
            return None
        recordings.update(ids | urls)
    return next(iter(recordings)) if len(recordings) == 1 else None


def music_context(job):
    """Return known sound ids and a pure, invocation-local attribution predicate."""
    job = _current_job(job)
    if job is None:
        return set(), lambda payload: False
    identities, music_ids = _index(job.connection.artist)

    def is_artist_music(payload):
        return isinstance(payload, dict) and (_id(payload.get("id"), numeric=True) in music_ids or _target(payload, identities) is not None)

    return music_ids, is_artist_music


@transaction.atomic
def attach_music_matches(conn, obj, source, row):
    """Add one accepted sound relationship, or return zero without changing rows."""
    payload = source.payload_json
    if row.endpoint != "tiktok-music" or row.kind != "music" or row.incomplete or not source.sync_run_id:
        return 0
    job = _current_job(source.sync_run.sync_job)
    if job is None or job.connection_id != conn.pk:
        return 0
    conn = job.connection
    music_id = _id(row.external_id, numeric=True)
    if not music_id or not isinstance(payload, dict) or (source.organization_id != conn.organization_id or
            source.service_id != conn.service_id or source.external_id != music_id or
            source.object_type != row.endpoint or _id(payload.get("id"), numeric=True) != music_id or
            not isinstance(row.payload, dict) or _id(row.payload.get("id"), numeric=True) != music_id):
        return 0
    obj = PlatformObject.objects.select_for_update().select_related("service").filter(pk=obj.pk).first()
    if obj is None or obj.service_id != conn.service_id or obj.object_type != "music" or obj.external_id != music_id or _music_id(obj) != music_id:
        return 0
    identities, _ = _index(conn.artist)
    recording_id = _target(payload, identities)
    if recording_id is None:
        return 0
    candidates = PlatformObject.objects.filter(service=conn.service, object_type__in=MUSIC_KINDS).filter(
        Q(external_id=music_id) | Q(url__contains=music_id)).select_related("service")
    aliases = [candidate.pk for candidate in candidates if candidate.external_id == music_id or _music_url_id(candidate.url) == music_id]
    if active_rows(PlatformObject.objects.filter(pk__in=aliases)).count() != len(aliases):
        return 0
    # All existing decisions count, including other workspaces, rejected rows,
    # removed links and legacy aliases. Never route around an operator decision.
    if RecordingPlatformMatch.objects.filter(platform_object_id__in=aliases).exists():
        return 0
    RecordingPlatformMatch.objects.create(recording_id=recording_id, platform_object=obj,
        relationship_type="sound", review_status="accepted", match_method="verified_streaming_link", confidence="1.000")
    return 1
