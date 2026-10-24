"""Artist-scoped presentation of public platform observations, never raw payloads."""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, CharField, F, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from analytics.models import MetricObservation
from catalog.models import ReleasePlatformMatch
from provenance.models import SourceRecord, SyncRun
from provenance.collectors.providers import PLATFORMS
from .access import artist_for
from .querysets import scoped
from .validation import safe_external_url
from .views import context


ENDPOINTS = {
    "artist-details", "user-details", "user-profile", "channel-details", "album-details",
    "track-details", "song-details", "spotify-track", "video-details", "post-details", "post-list-item",
    "artist-discovered-on", "artist-appears-on", "users-playlists", "users-spotlight",
    "users-social-links", "artists-music-videos", "artists-playlists", "channels-playlists",
}
COLLECTION_ENDPOINTS = {"artist-discovered-on", "artist-appears-on", "users-playlists", "users-spotlight", "users-social-links", "artists-music-videos", "artists-playlists", "channels-playlists"}
LABELS = {"artist-discovered-on": "Discovered on playlists", "artist-appears-on": "Appears on releases",
          "post-list-item": "Instagram post", "post-details": "Instagram post",
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
    from .recording_metrics import match_filter as identity_filter
    return identity_filter(obj)


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


def card(source, artist, *, metrics=None, recording_links=None):
    body = body_for(source)
    album = body.get("album") or {}
    return {"source": source, "title": body.get("name") or body.get("title") or body.get("username") or LABELS.get(source.object_type, source.object_type.replace("-", " ").title()),
            "subtitle": album.get("name") or body.get("album_name") or body.get("artist_name") or "",
            "image": image_for(body), "url": link(body.get("link") or (body.get("external_urls") or {}).get("spotify")),
            "detail_url": reverse("portal:insight-detail", args=[artist.pk, source.pk]),
            "metrics": metrics if metrics is not None else metric_cards(artist, source, history=False),
            "recording_links": recording_links or []}


def metric_cards_for_sources(artist, sources, *, history=False):
    """Fetch latest series and optional history in two queries for any card batch."""
    from .recording_metrics import SOURCE_KINDS, source_identity
    keys = {source_identity(source) for source in sources}
    condition = Q(pk__in=[])
    for service_id, kind, external_id in keys:
        if kind and external_id:
            condition |= Q(series__platform_object__service_id=service_id,
                series__platform_object__external_id=external_id,
                series__platform_object__object_type=kind,
                source_record__object_type__in=[endpoint for endpoint, source_kind in SOURCE_KINDS.items() if source_kind == kind])
    observations = MetricObservation.objects.filter(condition,
        source_record__in=sources_for(artist), quality_status__in=["pending", "accepted"],
        series__organization=artist.organization,
        source_record__service_id=F("series__platform_object__service_id"),
        source_record__external_id=F("series__platform_object__external_id"),
    ).select_related("series__metric_definition", "series__platform_object", "source_record")
    latest = list(observations.order_by("series_id", "-period_start", "-revision", "-observed_at", "-pk").distinct("series_id"))
    historical = list(observations.filter(period_start__gte=timezone.now() - timedelta(days=30)).order_by(
        "series_id", "period_start", "-revision", "-observed_at", "-pk").distinct("series_id", "period_start")) if history else []
    groups = {}
    for row in latest:
        obj, definition = row.series.platform_object, row.series.metric_definition
        source_key = (obj.service_id, obj.object_type, obj.external_id)
        metric_key = (source_key, definition.code, definition.definition_version, row.series.dimensions_hash)
        group = groups.setdefault(metric_key, {"latest": row, "rows": {}})
        previous = group["latest"]
        if (row.period_start, row.observed_at, row.revision, str(row.pk)) > (previous.period_start, previous.observed_at, previous.revision, str(previous.pk)):
            group["latest"] = row
    for row in historical:
        obj, definition = row.series.platform_object, row.series.metric_definition
        metric_key = ((obj.service_id, obj.object_type, obj.external_id), definition.code,
            definition.definition_version, row.series.dimensions_hash)
        if metric_key not in groups:
            continue
        day = row.period_start
        previous = groups[metric_key]["rows"].get(day)
        if previous is None or (row.observed_at, row.revision, str(row.pk)) > (previous.observed_at, previous.revision, str(previous.pk)):
            groups[metric_key]["rows"][day] = row
    result = {key: [] for key in keys}
    for (source_key, code, version, dimension_hash), group in sorted(groups.items(), key=lambda entry: (entry[0][1], entry[0][2], entry[0][3])):
        row = group["latest"]
        dims = row.series.dimensions_json
        label = code.replace("_", " ").capitalize()
        if dims.get("city"):
            label = f"{dims['city']} · {label}"
        values = sorted(group["rows"].values(), key=lambda value: value.period_start)
        delta = row.value - values[-2].value if len(values) > 1 and row.value is not None and values[-2].value is not None else None
        numbers = [float(value.value) for value in values if value.value is not None]
        points = ""
        if len(numbers) > 1:
            low, high = min(numbers), max(numbers)
            points = " ".join(f"{i * 220 / (len(numbers)-1):.1f},{50 - (number-low) * 46 / (high-low or 1):.1f}" for i, number in enumerate(numbers))
        result.setdefault(source_key, []).append({"label": label, "latest": row, "history": values, "delta": delta, "points": points})
    return result


def metric_cards(artist, source, *, history):
    from .recording_metrics import source_identity
    return metric_cards_for_sources(artist, [source], history=history).get(source_identity(source), [])


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
    from .recording_metrics import (SOURCE_KINDS, accepted_recording_matches, recording_source_filter,
        recording_links_for_sources, source_identity)
    recording = None
    if "recording" in request.GET:
        try:
            recording = get_object_or_404(scoped(request.user, artist, "recordings"), pk=request.GET["recording"])
        except (ValidationError, ValueError):
            raise Http404 from None
        sources = sources.filter(recording_source_filter(artist, recording))
    release = None
    if request.GET.get("release"):
        try:
            release = get_object_or_404(scoped(request.user, artist, "releases"), pk=request.GET["release"])
        except (ValidationError, ValueError):
            raise Http404 from None
        recordings = scoped(request.user, artist, "tracks").filter(release=release).values("recording_id")
        matches = accepted_recording_matches(artist).filter(recording_id__in=recordings)
        from .querysets import active_rows
        albums = active_rows(ReleasePlatformMatch.objects.filter(release=release, review_status="accepted"))
        locator_filter = Q(pk__in=[])
        for obj in [m.platform_object for m in matches.select_related("platform_object__service")] + [m.platform_object for m in albums.select_related("platform_object__service")]:
            locator_filter |= match_filter(obj)
        sources = sources.filter(locator_filter)
    sources = sources.annotate(identity_kind=Case(
        *[When(object_type=endpoint, then=Value(kind)) for endpoint, kind in SOURCE_KINDS.items()],
        default=F("object_type"), output_field=CharField(),
    )).order_by("service_id", "identity_kind", "external_id", "-observed_at", "-created_at", "-pk").distinct("service_id", "identity_kind", "external_id")
    page = Paginator(sources.select_related("service"), 24).get_page(request.GET.get("page"))
    page_sources = list(page)
    metrics = metric_cards_for_sources(artist, page_sources)
    recording_links = recording_links_for_sources(artist, page_sources)
    cards = [card(source, artist, metrics=metrics.get(source_identity(source), []), recording_links=recording_links[source.pk]) for source in page_sources]
    runs = SyncRun.objects.filter(sync_job__connection__artist=artist, sync_job__connection__organization=artist.organization, sync_job__job_type="daily_public_platform").select_related("sync_job__connection__service").order_by("sync_job_id", "-created_at").distinct("sync_job_id")
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "portal/insights.html", context(request, artist, cards=cards, page_obj=page,
        platforms=sorted(PLATFORMS), platform=platform, query=query, runs=runs, selected_release=release, selected_recording=recording,
        filter_query=params.urlencode()))


@login_required
@require_GET
def detail(request, artist_id, source_id):
    artist = artist_for(request.user, artist_id)
    source = get_object_or_404(sources_for(artist).select_related("service"), pk=source_id)
    from .recording_metrics import recording_links_for_sources
    recording_links = recording_links_for_sources(artist, [source])[source.pk]
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
    metrics = metric_cards(artist, source, history=True)
    return render(request, "portal/insight_detail.html", context(request, artist,
        item=card(source, artist, metrics=metrics, recording_links=recording_links),
        facts=facts, sections=sections, preview=preview, metrics=metrics))
