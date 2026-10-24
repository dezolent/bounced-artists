"""Public TikTok performance from scoped snapshots and unrejected observations."""
from collections import Counter
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from analytics.models import MetricObservation
from provenance.models import SourceRecord
from .recording_metrics import accepted_recording_matches, locator
from .validation import safe_external_url

KINDS = {"tiktok-user": "user", "tiktok-video": "video", "tiktok-music": "music", "tiktok-usage-video": "usage_video"}
COUNTERS = {"views": "view_count", "likes": "like_count", "comments": "comment_count", "shares": "share_count", "saves": "save_count", "reposts": "repost_count"}
ENGAGEMENT = ("likes", "comments", "shares", "saves")


def object_dict(value):
    return value if isinstance(value, dict) else {}


def text(value, limit=300):
    return value[:limit] if isinstance(value, str) else ""


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def native_link(value):
    try:
        value = safe_external_url(value) if isinstance(value, str) else ""
        parsed = urlsplit(value or "")
    except (ValidationError, ValueError):
        return ""
    if parsed.hostname not in {"www.tiktok.com", "tiktok.com"}:
        return ""
    return urlunsplit(("https", parsed.hostname, parsed.path, "", ""))


def posted_at(body):
    value = body.get("created_at") or body.get("published_at") or body.get("create_time")
    if isinstance(value, str):
        try:
            result = parse_datetime(value)
        except ValueError:
            result = None
        if result:
            return result.replace(tzinfo=datetime_timezone.utc) if result.tzinfo is None else result
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=datetime_timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    return None


def engagement(metrics):
    views = metrics.get("views")
    if views is None or views <= 0 or any(metrics.get(code) is None for code in ENGAGEMENT):
        return None
    return sum((metrics[code] for code in ENGAGEMENT), Decimal(0)) / views * 100


def current_metrics(artist, sources):
    """No raw stats fallback: rejected/missing counters remain unavailable."""
    if not sources:
        return {}
    kinds = Q(pk__in=[])
    for endpoint, kind in KINDS.items():
        kinds |= Q(source_record__object_type=endpoint, series__platform_object__object_type=kind)
    observations = MetricObservation.objects.filter(kinds, source_record_id__in=[source.pk for source in sources],
        quality_status__in=["pending", "accepted"], series__organization_id=artist.organization_id,
        source_record__organization_id=artist.organization_id,
        series__platform_object__service_id=F("source_record__service_id"),
        series__platform_object__external_id=F("source_record__external_id"),
        series__metric_definition__service_id=F("source_record__service_id"),
        series__metric_definition__unit="count", series__dimensions_json={"scope": "public"},
    ).order_by("series_id", "-period_start", "-revision", "-observed_at", "-pk").distinct("series_id").values(
        "pk", "source_record_id", "value", "period_start", "observed_at", "revision",
        "series__metric_definition__code", "series__metric_definition__definition_version")
    chosen = {}
    for observation in observations:
        key = observation["source_record_id"], observation["series__metric_definition__code"]
        rank = (observation["period_start"], observation["observed_at"], observation["series__metric_definition__definition_version"], observation["revision"], str(observation["pk"]))
        if key not in chosen or rank > chosen[key][0]:
            chosen[key] = (rank, number(observation["value"]))
    result = {}
    for (source_id, code), (_, value) in chosen.items():
        result.setdefault(source_id, {})[code] = value
    return result


def row_for(artist, source, metrics):
    body = object_dict(source.payload_json)
    author, music = object_dict(body.get("author")), object_dict(body.get("music"))
    counters = {code: metrics.get(native) for code, native in COUNTERS.items()}
    tags = body.get("hashtags") if isinstance(body.get("hashtags"), list) else []
    hashtags = [text(tag if isinstance(tag, str) else object_dict(tag).get("name"), 100).lstrip("#") for tag in tags]
    video = object_dict(body.get("video"))
    seconds = number(body.get("duration_seconds", video.get("duration_seconds")))
    if seconds is None:
        milliseconds = number(body.get("duration_ms", video.get("duration_ms")))
        seconds = milliseconds / 1000 if milliseconds is not None else None
    return {"source": source, "detail_url": reverse("portal:insight-detail", args=[artist.pk, source.pk]),
        "url": native_link(body.get("link") or body.get("url")),
        "caption": text(body.get("description") or body.get("caption") or body.get("title")) or "Untitled video",
        "creator": text(author.get("username") or author.get("unique_id") or body.get("username"), 100),
        "posted_at": posted_at(body), "duration_seconds": seconds,
        "hashtags": [tag for tag in hashtags if tag], "music_id": str(music.get("id") or ""),
        "metrics": counters, "engagement_rate": engagement(counters)}


def music_identity(obj):
    if obj.object_type not in {"music", "recording", "track"}:
        return ""
    slug, kind, external_id, url = locator(obj)
    if slug != "tiktok" or kind != "music":
        return ""
    explicit = obj.external_id
    if explicit and not (explicit.isascii() and explicit.isdigit()):
        return ""
    if obj.url:
        # A supplied URL must be a native sound locator and must agree with any
        # explicit ID. Never reinterpret a video or contradictory sound as music.
        parsed = urlsplit(url)
        parts = parsed.path.strip("/").split("/")
        if parsed.hostname not in {"tiktok.com", "m.tiktok.com"} or len(parts) != 2 or parts[0] != "music":
            return ""
        linked = parts[-1].rsplit("-", 1)[-1]
        if not (linked.isascii() and linked.isdigit()) or (explicit and explicit != linked):
            return ""
        return linked
    return external_id if external_id and external_id.isascii() and external_id.isdigit() else ""


def performance_for(artist):
    sources = list(SourceRecord.objects.filter(organization_id=artist.organization_id,
        service__slug="tiktok", collector="omkar", object_type__in=KINDS,
        sync_run__sync_job__job_type="daily_public_platform",
        sync_run__sync_job__connection__artist=artist,
        sync_run__sync_job__connection__organization_id=artist.organization_id,
        service_id=F("sync_run__sync_job__connection__service_id"),
    ).order_by("object_type", "external_id", "-observed_at", "-created_at", "-pk").distinct("object_type", "external_id"))
    metrics = current_metrics(artist, sources)
    owned, music, profiles, usage_candidates = [], [], [], []
    for source in sources:
        row = row_for(artist, source, metrics.get(source.pk, {}))
        body = object_dict(source.payload_json)
        if source.object_type == "tiktok-video":
            owned.append(row)
        elif source.object_type == "tiktok-usage-video":
            row["music_id"] = str(object_dict(body.get("_collection")).get("music_id") or "")
            usage_candidates.append(row)
        elif source.object_type == "tiktok-music":
            row.update(title=text(body.get("title") or body.get("name")) or "Untitled sound",
                       author=text(body.get("author_name")), video_count=metrics.get(source.pk, {}).get("video_count"), recording_links=[])
            music.append(row)
        else:
            profile_metrics = metrics.get(source.pk, {})
            row.update(title=text(body.get("nickname") or body.get("username")) or "TikTok account",
                profile_metrics=[{"label": label, "value": profile_metrics.get(code)} for code, label in (
                    ("follower_count", "Followers"), ("following_count", "Following"),
                    ("like_count", "Account likes"), ("video_count", "Reported videos"))])
            profiles.append(row)
    owned_ids = {row["source"].external_id for row in owned}
    music_ids = {row["source"].external_id for row in music}
    usage = [row for row in usage_candidates if row["source"].external_id not in owned_ids and row["music_id"] in music_ids]
    sample_counts = Counter(row["music_id"] for row in usage)
    music_by_id = {row["source"].external_id: row for row in music}
    for row in music:
        row["sample_count"] = sample_counts[row["source"].external_id]
    for row in usage:
        row["music_title"] = music_by_id[row["music_id"]]["title"]
    if music:
        for match in accepted_recording_matches(artist):
            identity = music_identity(match.platform_object)
            if identity in music_by_id:
                links = music_by_id[identity]["recording_links"]
                if not any(link["recording"].pk == match.recording_id for link in links):
                    links.append({"recording": match.recording, "url": reverse("portal:recording", args=[artist.pk, match.recording_id])})
    owned.sort(key=lambda row: (row["metrics"]["views"] is None, -(row["metrics"]["views"] or Decimal(0)), row["source"].external_id))
    usage.sort(key=lambda row: (row["posted_at"] is not None, row["posted_at"] or datetime.min.replace(tzinfo=datetime_timezone.utc), row["source"].observed_at), reverse=True)
    music.sort(key=lambda row: (row["video_count"] is None, -(row["video_count"] or Decimal(0)), row["title"]))
    totals = {}
    for code in COUNTERS:
        measured = [row["metrics"][code] for row in owned if row["metrics"][code] is not None]
        totals[code] = {"value": sum(measured, Decimal(0)) if measured else None, "count": len(measured)}
    complete = [row for row in owned if row["engagement_rate"] is not None]
    rate = engagement({code: sum((row["metrics"][code] for row in complete), Decimal(0)) for code in ("views", *ENGAGEMENT)}) if complete else None
    return {"owned_videos": owned, "music": music, "profiles": profiles, "usage_videos": usage,
        "totals": totals, "collected_video_count": len(owned), "usage_sample_count": len(usage),
        "engagement_rate": rate, "engagement_complete_count": len(complete),
        "latest": max((source.observed_at for source in sources), default=None), "has_data": bool(sources)}
