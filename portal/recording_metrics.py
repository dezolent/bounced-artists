"""Read-only links from accepted, granted catalog identities to collected metrics."""
from urllib.parse import parse_qs, urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from catalog.models import Recording, RecordingPlatformMatch
from .querysets import active_rows
from .validation import safe_external_url


SOURCE_KINDS = {"artist-details": "artist", "user-details": "user", "user-profile": "user",
    "channel-details": "channel", "album-details": "album", "track-details": "track",
    "song-details": "track", "spotify-track": "track", "video-details": "video", "post-details": "post", "post-list-item": "post",
    "tiktok-user": "user", "tiktok-video": "video", "tiktok-music": "music", "tiktok-usage-video": "usage_video"}


def canonical_url(value):
    try:
        value = safe_external_url(value) if isinstance(value, str) else ""
    except (ValidationError, ValueError):
        return ""
    if not value:
        return ""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))


def locator(obj):
    """Decode only platform locators; titles and recording identifiers are never joins."""
    slug = obj.service.slug
    kind = {"recording": "track", "release": "album"}.get(obj.object_type, obj.object_type)
    external_id = obj.external_id
    url = canonical_url(obj.url)
    parsed = urlsplit(url)
    original = urlsplit(obj.url) if url else parsed
    parts = parsed.path.strip("/").split("/")
    if slug == "spotify" and parsed.hostname == "open.spotify.com" and len(parts) >= 2 and parts[-2] in {"track", "album"}:
        kind, external_id = parts[-2:]
    elif slug == "apple-music" and parsed.hostname == "music.apple.com":
        song = parse_qs(original.query).get("i", [None])[0]
        if song:
            kind, external_id = "track", song
        elif len(parts) >= 3 and parts[1] in {"album", "song"}:
            kind, external_id = ("album" if parts[1] == "album" else "track"), parts[-1]
        elif len(parts) == 2 and parts[0] == "song":
            kind, external_id = "track", parts[-1]
    elif slug in {"youtube", "youtube-music"}:
        slug, kind = "youtube", "video"
        if parsed.hostname in {"youtube.com", "music.youtube.com"}:
            external_id = parse_qs(original.query).get("v", [None])[0] or (parts[-1] if parts[0] in {"shorts", "live"} else external_id)
        elif parsed.hostname == "youtu.be":
            external_id = parts[-1]
    elif slug == "instagram":
        kind = "post"
        if len(parts) == 2 and parts[0] in {"p", "reel"}:
            external_id = external_id or parts[1]
    elif slug == "tiktok":
        if kind in {"track", "music"}:
            kind = "music"
        if parsed.hostname == "tiktok.com":
            if len(parts) == 2 and parts[0] == "music":
                music_id = parts[-1].rsplit("-", 1)[-1]
                if music_id.isascii() and music_id.isdigit():
                    kind, external_id = "music", music_id
            elif len(parts) == 3 and parts[0].startswith("@") and parts[1] == "video":
                kind = "usage_video" if kind == "usage_video" else "video"
                external_id = parts[-1]
    return slug, kind, external_id, url


def match_filter(obj, *, recording_only=False):
    slug, kind, external_id, url = locator(obj)
    if recording_only and kind not in {"track", "video", "post", "music"}:
        return Q(pk__in=[])
    endpoints = [endpoint for endpoint, source_kind in SOURCE_KINDS.items() if source_kind == kind]
    identity = Q(external_id=external_id) if external_id else Q(pk__in=[])
    # SoundCloud exposes a numeric id in metrics while legacy catalog rows keep a permalink.
    # Query-free path matching permits share parameters without broad title matching.
    if url and slug == "soundcloud":
        parsed = urlsplit(url)
        for host in (parsed.hostname, "www." + parsed.hostname):
            base = f"https://{host}{parsed.path}"
            for key in ("link", "url", "permalink_url"):
                identity |= Q(**{f"payload_json__{key}": base}) | Q(**{f"payload_json__{key}": base + "/"}) | Q(**{f"payload_json__{key}__startswith": base + "?"})
    elif url and not external_id:
        identity |= Q(payload_json__link=url) | Q(payload_json__link=url + "/")
    if slug == "instagram" and external_id:
        identity |= Q(payload_json__code=external_id)
    return Q(service__slug=slug, object_type__in=endpoints) & identity


def accepted_recording_matches(artist, recording=None):
    recordings = active_rows(Recording.objects.filter(organization=artist.organization,
        artist_grants__artist=artist, archived_at__isnull=True))
    if recording is not None:
        recordings = recordings.filter(pk=recording.pk)
    return active_rows(RecordingPlatformMatch.objects.filter(recording__in=recordings,
        review_status="accepted")).select_related("recording", "platform_object__service")


def recording_source_filter(artist, recording=None):
    condition = Q(pk__in=[])
    for match in accepted_recording_matches(artist, recording):
        condition |= match_filter(match.platform_object, recording_only=True)
    return condition


def source_identity(source):
    return source.service_id, SOURCE_KINDS.get(source.object_type), source.external_id


def latest_sources(sources):
    """Collapse official and scraper endpoints representing the same platform object."""
    result = {}
    for source in sources:
        key = source_identity(source)
        # Collection endpoint records without object ids must not merge with each other.
        if not key[1] or not key[2]:
            key = (*key, source.object_type, source.pk)
        previous = result.get(key)
        if previous is None or (source.observed_at, source.created_at, str(source.pk)) > (previous.observed_at, previous.created_at, str(previous.pk)):
            result[key] = source
    return list(result.values())


def recording_links_for_sources(artist, sources):
    result = {source.pk: [] for source in sources}
    for match in accepted_recording_matches(artist):
        slug, kind, external_id, url = locator(match.platform_object)
        if kind not in {"track", "video", "post", "music"}:
            continue
        for source in sources:
            if source.service.slug != slug or SOURCE_KINDS.get(source.object_type) != kind:
                continue
            body = source.payload_json or {}
            urls = {canonical_url(body.get(key)) for key in ("link", "url", "permalink_url")}
            identity_matches = bool(external_id and (source.external_id == external_id or (slug == "instagram" and body.get("code") == external_id)))
            if identity_matches or (slug == "soundcloud" and url and url in urls):
                if any(row["recording"].pk == match.recording_id for row in result[source.pk]):
                    continue
                result[source.pk].append({"recording": match.recording,
                    "url": reverse("portal:recording", args=[artist.pk, match.recording_id])})
    return result


def catalog_metadata(artist, source):
    """Display public catalog claims separately from canonical edits and metrics."""
    from .insights import body_for, display_rows
    body = body_for(source)
    rows = []
    for key in ('name','title','version_title','artist_name','album_name','composer_name',
                'release_date','release_date_precision','duration_ms','duration','explicit','is_explicit',
                'audio_language','language','genres','genre','isrc','external_ids','track_number','disc_number',
                'record_label','label','copyright','copyrights','license','audio_traits','has_lyrics',
                'has_time_synced_lyrics','is_apple_digital_master','is_mastered_for_itunes',
                'artists','composers','credits','publisher','album','preview_link','preview_url'):
        if key == 'duration_ms' and type(body.get(key)) is int:
            from .catalog_display import duration
            rows.append({'label':'Duration','text':duration(body[key]),'url':''})
        elif key in {'explicit','is_explicit','has_lyrics','has_time_synced_lyrics','is_apple_digital_master','is_mastered_for_itunes'} and type(body.get(key)) is bool:
            label = {'explicit':'Explicit content','is_explicit':'Explicit content'}.get(key,key.replace('_',' ').capitalize())
            rows.append({'label':label,'text':'Yes' if body[key] else 'No','url':''})
        else:
            rows.extend(display_rows(body.get(key), {'isrc':'ISRC','external_ids':'Identifiers'}.get(key,key.replace('_',' '))))
    return {'source':source,'rows':rows[:80],
            'detail_url':reverse('portal:insight-detail',args=[artist.pk,source.pk])}


def recording_metrics(artist, recording):
    from .insights import metric_cards_for_sources, sources_for
    current_sources = sources_for(artist).filter(recording_source_filter(artist, recording)).select_related("service").order_by(
        "service_id", "object_type", "external_id", "-observed_at", "-created_at", "-pk",
    ).distinct("service_id", "object_type", "external_id")
    current_sources = list(current_sources)
    sources = latest_sources(current_sources)
    metrics = metric_cards_for_sources(artist, sources, history=True)
    now = timezone.now()
    return [{"source": source, "metrics": metrics.get(source_identity(source), []),
        "detail_url": reverse("portal:insight-detail", args=[artist.pk, source.pk]),
        "catalog_metadata": [catalog_metadata(artist, candidate) for candidate in current_sources
            if source_identity(candidate) == source_identity(source)],
        "stale": (now - source.observed_at).total_seconds() > 2 * 86400,
        "insights_url": reverse("portal:insights", args=[artist.pk]) + f"?recording={recording.pk}"}
        for source in sorted(sources, key=lambda row: (row.service.name, row.external_id))]
