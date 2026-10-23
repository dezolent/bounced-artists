"""Artist-scoped presentation of public platform observations, never raw payloads."""
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit, urlunsplit

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from analytics.models import MetricObservation
from catalog.models import RecordingPlatformMatch, ReleasePlatformMatch
from provenance.models import SourceRecord, SyncRun
from provenance.collectors.providers import PLATFORMS
from .access import artist_for
from .querysets import scoped
from .validation import safe_external_url
from .views import context


ENDPOINTS = {
    "artist-details", "user-details", "user-profile", "channel-details", "album-details",
    "track-details", "song-details", "spotify-track", "video-details", "post-details",
    "artist-discovered-on", "artist-appears-on", "users-playlists", "users-spotlight",
    "users-social-links", "artists-music-videos", "artists-playlists", "channels-playlists",
}
COLLECTION_ENDPOINTS = {"artist-discovered-on", "artist-appears-on", "users-playlists", "users-spotlight", "users-social-links", "artists-music-videos", "artists-playlists", "channels-playlists"}
LABELS = {"artist-discovered-on": "Discovered on playlists", "artist-appears-on": "Appears on releases",
          "users-playlists": "SoundCloud playlists", "users-spotlight": "SoundCloud spotlight",
          "users-social-links": "Social links", "artists-music-videos": "Music videos",
          "artists-playlists": "Artist playlists", "channels-playlists": "Channel playlists"}


def sources_for(artist):
    return SourceRecord.objects.filter(organization=artist.organization,
        sync_run__sync_job__connection__artist=artist,
        sync_run__sync_job__connection__organization=artist.organization,
        object_type__in=ENDPOINTS, collector__in=["omkar", "spotify-web-api"])


def link(value):
    try:
        return (safe_external_url(value) or "") if isinstance(value, str) else ""
    except ValidationError:
        return ""


def body_for(source):
    body = source.payload_json or {}
    if source.object_type == "video-details":
        return body.get("video") or {}
    if source.object_type == "channel-details":
        return body.get("channel") or {}
    return body


def match_filter(obj):
    """Resolve accepted legacy URL-only destinations without modifying their rows."""
    slug = obj.service.slug
    parsed = urlsplit(link(obj.url))
    parts = parsed.path.strip("/").split("/")
    kind = {"recording": "track", "release": "album"}.get(obj.object_type, obj.object_type)
    external_id = obj.external_id
    if slug == "spotify" and parsed.hostname == "open.spotify.com" and len(parts) >= 2:
        if parts[-2] in {"track", "album"}:
            kind, external_id = parts[-2], parts[-1]
    elif slug == "apple-music" and parsed.hostname == "music.apple.com":
        song = parse_qs(parsed.query).get("i", [None])[0]
        if song:
            kind, external_id = "track", song
        elif len(parts) >= 3 and parts[1] in {"album", "song"}:
            kind, external_id = ("album" if parts[1] == "album" else "track"), parts[-1]
    elif slug in {"youtube", "youtube-music"}:
        kind = "video"
        if parsed.hostname in {"youtube.com", "www.youtube.com", "music.youtube.com"}:
            external_id = parse_qs(parsed.query).get("v", [None])[0] or (parts[-1] if parts[0] in {"shorts", "live"} else external_id)
        elif parsed.hostname == "youtu.be":
            external_id = parts[-1]
        slug = "youtube"
    elif slug == "instagram":
        kind = "post"
    endpoints = {"album": ["album-details"], "track": ["track-details", "song-details", "spotify-track"], "video": ["video-details"], "post": ["post-details"]}.get(kind, [])
    identity = Q(external_id=external_id) if external_id else Q(pk__in=[])
    if link(obj.url):
        canonical = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        identity |= Q(payload_json__link=canonical) | Q(payload_json__link=canonical + "/")
        if slug == "instagram" and parts[0] in {"p", "reel"} and len(parts) == 2:
            identity |= Q(payload_json__code=parts[1])
    return Q(service__slug=slug, object_type__in=endpoints) & identity


def image_for(body):
    for value in (body.get("artwork"), body.get("avatar"), body.get("images"),
                  body.get("thumbnail"), body.get("image"), body.get("profile_picture"),
                  (body.get("album") or {}).get("images")):
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("link") or value.get("url")
        if link(value):
            return link(value)
    return ""


def card(source, artist):
    body = body_for(source)
    album = body.get("album") or {}
    return {"source": source, "title": body.get("name") or body.get("title") or body.get("username") or LABELS.get(source.object_type, source.object_type.replace("-", " ").title()),
            "subtitle": album.get("name") or body.get("album_name") or body.get("artist_name") or "",
            "image": image_for(body), "url": link(body.get("link") or (body.get("external_urls") or {}).get("spotify")),
            "detail_url": reverse("portal:insight-detail", args=[artist.pk, source.pk]),
            "metrics": metric_cards(artist, source, history=False)}


def metric_cards(artist, source, *, history):
    # Scope by the source artist as well as organization: platform objects are global.
    kind = {"artist-details": "artist", "user-details": "user", "user-profile": "user",
            "channel-details": "channel", "album-details": "album", "track-details": "track",
            "song-details": "track", "spotify-track": "track", "video-details": "video", "post-details": "post"}.get(source.object_type)
    if kind is None:
        return []
    observations = MetricObservation.objects.filter(
        source_record__in=sources_for(artist), quality_status__in=["pending", "accepted"],
        series__organization=artist.organization, series__platform_object__service=source.service,
        series__platform_object__external_id=source.external_id,
        series__platform_object__object_type=kind,
    ).select_related("series__metric_definition")
    latest = observations.order_by("series_id", "-period_start", "-revision").distinct("series_id")
    result = []
    for row in latest:
        code = row.series.metric_definition.code
        dims = row.series.dimensions_json
        label = code.replace("_", " ").capitalize()
        if dims.get("city"):
            label = f"{dims['city']} · {label}"
        values = []
        if history:
            values = list(observations.filter(series=row.series, observed_at__gte=timezone.now() - timedelta(days=30)).order_by("period_start", "-revision").distinct("period_start"))
        delta = row.value - values[-2].value if len(values) > 1 and row.value is not None and values[-2].value is not None else None
        numbers = [float(v.value) for v in values if v.value is not None]
        points = ""
        if len(numbers) > 1:
            low, high = min(numbers), max(numbers)
            points = " ".join(f"{i * 220 / (len(numbers)-1):.1f},{50 - (n-low) * 46 / (high-low or 1):.1f}" for i,n in enumerate(numbers))
        result.append({"label": label, "latest": row, "history": values, "delta": delta, "points": points})
    return result


# Only known public metadata is presented. Unexpected fields stay in private evidence.
FACT_FIELDS = ("id", "name", "title", "username", "artist_name", "album_name", "composer_name",
    "description", "biography", "bio", "caption", "genre", "genres", "tags", "label", "record_label",
    "license", "release_date", "release_date_precision", "duration_ms", "duration_seconds", "duration",
    "track_number", "disc_number", "track_count", "total_tracks", "isrc", "upc", "copyright",
    "copyrights", "is_explicit", "is_playable", "is_verified", "published_at", "published_date",
    "created_at", "updated_at", "origin", "city", "country", "country_code", "audio_language",
    "audio_traits", "has_lyrics", "has_captions", "is_apple_digital_master", "is_live", "category")
SECTIONS = ("stats", "external_ids", "artists", "composers", "album", "credits", "publisher", "external_links", "social_links",
    "bio_links", "description_links", "images", "header_images", "gallery", "artwork", "editorial_artwork",
    "top_cities", "top_tracks", "top_songs", "latest_release", "popular_releases", "discography_counts", "labels", "results", "items", "playlists",
    "music_videos", "chapters", "music", "subtitles", "owner", "audio", "video", "preview", "primary_genre")
NESTED_FIELDS = set(FACT_FIELDS) | {"link", "url", "role", "role_group", "contributors", "text", "type", "width", "height", "listeners", "region", "followers", "monthly_listeners", "world_rank", "plays", "likes", "reposts", "comments", "downloads", "tracks", "playlists", "name", "artist_id", "is_preview_only", "p_line", "c_line", "writers", "thumbnail", "image", "profile_picture", "network", "format", "start_time", "end_time", "start_seconds", "language", "language_code", "is_auto_generated"} | set(SECTIONS)
NESTED_FIELDS |= {"short", "full", "tagline", "albums", "singles", "compilations", "eps", "ean", "spotify", "subscriber_count", "video_count", "view_count", "like_count", "comment_count", "following_count", "post_count", "play_count", "repost_count"}


def display_rows(value, prefix="", *, depth=0):
    if depth > 6:
        return []
    if isinstance(value, dict):
        result = []
        for key, child in value.items():
            if key in NESTED_FIELDS:
                result.extend(display_rows(child, f"{prefix} · {key.replace('_', ' ')}".strip(" ·"), depth=depth+1))
        return result
    if isinstance(value, list):
        result = []
        for index, child in enumerate(value[:300], 1):
            result.extend(display_rows(child, f"{prefix} {index}", depth=depth+1))
        return result
    if value is None or value == "":
        return []
    if isinstance(value, str) and (value.startswith(("http:", "https:", "javascript:", "data:"))):
        safe = link(value)
        return [{"label": prefix.capitalize(), "text": "Open source link", "url": safe}] if safe else []
    return [{"label": prefix.capitalize(), "text": str(value), "url": ""}]


@login_required
@require_GET
def insights(request, artist_id):
    artist = artist_for(request.user, artist_id)
    sources = sources_for(artist)
    platform = request.GET.get("platform", "")
    if platform in PLATFORMS:
        sources = sources.filter(service__slug=platform)
    query = request.GET.get("q", "")[:200]
    if query:
        sources = sources.filter(Q(payload_json__name__icontains=query) | Q(payload_json__title__icontains=query) | Q(payload_json__album__name__icontains=query) | Q(payload_json__album_name__icontains=query) | Q(payload_json__video__title__icontains=query))
    release = None
    if request.GET.get("release"):
        try:
            release = get_object_or_404(scoped(request.user, artist, "releases"), pk=request.GET["release"])
        except (ValidationError, ValueError):
            raise Http404 from None
        recordings = scoped(request.user, artist, "tracks").filter(release=release).values("recording_id")
        matches = RecordingPlatformMatch.objects.filter(recording_id__in=recordings, review_status="accepted")
        albums = ReleasePlatformMatch.objects.filter(release=release, review_status="accepted")
        locator_filter = Q(pk__in=[])
        for obj in [m.platform_object for m in matches.select_related("platform_object__service")] + [m.platform_object for m in albums.select_related("platform_object__service")]:
            locator_filter |= match_filter(obj)
        sources = sources.filter(locator_filter)
    sources = sources.order_by("service_id", "object_type", "external_id", "-observed_at", "-created_at").distinct("service_id", "object_type", "external_id")
    page = Paginator(sources.select_related("service"), 24).get_page(request.GET.get("page"))
    cards = [card(source, artist) for source in page]
    runs = SyncRun.objects.filter(sync_job__connection__artist=artist, sync_job__connection__organization=artist.organization, sync_job__job_type="daily_public_platform").select_related("sync_job__connection__service").order_by("sync_job_id", "-created_at").distinct("sync_job_id")
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "portal/insights.html", context(request, artist, cards=cards, page_obj=page,
        platforms=sorted(PLATFORMS), platform=platform, query=query, runs=runs, selected_release=release,
        filter_query=params.urlencode()))


@login_required
@require_GET
def detail(request, artist_id, source_id):
    artist = artist_for(request.user, artist_id)
    source = get_object_or_404(sources_for(artist).select_related("service"), pk=source_id)
    body = body_for(source)
    facts = []
    for key in FACT_FIELDS:
        if key == "duration_ms" and body.get(key) is not None:
            from .catalog_display import duration
            facts.append({"label": "Duration", "text": duration(body[key]), "url": ""})
            continue
        facts.extend(display_rows(body.get(key), key.replace("_", " ")))
    sections = []
    for key in SECTIONS:
        rows = display_rows(body.get(key), key.replace("_", " "))
        if rows:
            sections.append({"title": key.replace("_", " ").capitalize(), "rows": rows})
    preview = link(body.get("preview_link") or body.get("preview_url"))
    return render(request, "portal/insight_detail.html", context(request, artist, item=card(source, artist),
        facts=facts, sections=sections, preview=preview, metrics=metric_cards(artist, source, history=True)))
