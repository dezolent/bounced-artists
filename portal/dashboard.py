"""Scoped dashboard counters; snapshots, revisions and aliases are never summed."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Max, Q
from django.urls import reverse
from django.utils import timezone

from analytics.models import MetricObservation
from artists.customer_workspaces import collection_artists
from catalog.brand_library import brand_logo
from provenance.models import SourceRecord, SyncRun
from .access import artists_for


PLATFORMS = {"shazam":"Shazam", "musixmatch":"Musixmatch", "spotify": "Spotify", "soundcloud": "SoundCloud", "apple-music": "Apple Music", "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
KINDS = {"shazam-song-details":"track", "shazam-audio-analysis":"track", "shazam-popular-segments":"track",
         "musixmatch-track-details":"track", "musixmatch-track-lyrics":"track", "musixmatch-writers":"track", "musixmatch-credits":"track","artist-details": "artist", "user-details": "user", "user-profile": "user", "channel-details": "channel",
         "track-details": "track", "song-details": "track", "spotify-track": "track", "album-details": "album",
         "video-details": "video", "post-details": "post", "post-list-item": "post",
         "tiktok-user": "user", "tiktok-video": "video", "tiktok-music": "music", "tiktok-usage-video": "usage_video"}
LABELS = {"plays": "Track plays", "views": "Content views", "followers": "Follows & subscriptions", "monthly_listeners": "Monthly Spotify listeners",
          "channel_views": "Channel views", "reel_plays": "Reel plays", "likes": "Likes", "comments": "Comments", "reposts": "Reposts", "downloads": "Downloads",
          "shares": "Shares", "saves": "Saves", "music_uses": "Videos using artist sounds",
          "profile_likes": "Lifetime profile likes", "published_videos": "Published videos",
          "usage_views": "Views on collected music uses"}
HEADLINES = ("plays", "views", "followers", "monthly_listeners")
PROFILE_KINDS = {"artist", "user", "channel"}
ARTIST_FIELD = "source_record__sync_run__sync_job__connection__artist_id"
FIELDS = ("id", "series_id", "value", "revision", "period_start", "observed_at", ARTIST_FIELD,
          "series__platform_object_id", "series__platform_object__object_type", "series__platform_object__external_id",
          "series__metric_definition__service__slug", "series__metric_definition__code", "series__dimensions_json")


def _measurement(row):
    dims = row["series__dimensions_json"] or {}
    if any(k != "scope" and v not in (None, "") for k, v in dims.items()):
        return None  # City/country slices must not inflate an account counter.
    kind, code = row["series__platform_object__object_type"], row["series__metric_definition__code"]
    platform = row["series__metric_definition__service__slug"]
    if kind in PROFILE_KINDS:
        if platform == "tiktok" and kind == "user":
            if code == "like_count":
                return "profile_likes"
            if code == "video_count":
                return "published_videos"
        if code in {"followers", "follower_count", "subscriber_count"}:
            return "followers"
        if code == "monthly_listeners" and platform == "spotify":
            return code
        if code == "view_count" and kind == "channel":
            return "channel_views"
    elif kind == "track" and code in {"plays", "playcount", "play_count"}:
        return "plays"
    elif kind in {"video", "post"} and code == "view_count":
        return "views"
    elif kind == "post" and code == "play_count":
        return "reel_plays"
    elif platform == "tiktok" and kind == "music" and code == "video_count":
        return "music_uses"
    elif platform == "tiktok" and kind == "usage_video" and code == "view_count":
        return "usage_views"
    if kind in {"track", "video", "post"}:
        return {"likes": "likes", "like_count": "likes", "comments": "comments", "comment_count": "comments",
                "reposts": "reposts", "repost_count": "reposts", "downloads": "downloads",
                "share_count": "shares", "save_count": "saves"}.get(code)
    return None


def _key(row, code):
    return (row["series__metric_definition__service__slug"], row["series__platform_object__object_type"],
            row["series__platform_object__external_id"], code)


def _rank(row):
    # SQL already picked the latest revision within each series. Independent
    # source/alias series have unrelated revision counters: compare their times.
    return (row["period_start"], row["observed_at"], row["revision"], str(row["id"]))


def _current(rows):
    chosen = {}
    for row in rows:
        code = _measurement(row)
        if not code:
            continue
        key = _key(row, code)
        if key not in chosen or _rank(row) > _rank(chosen[key]):
            chosen[key] = row
    return chosen


def _totals(chosen):
    grouped = defaultdict(list)
    for key, row in chosen.items():
        if row["value"] is not None and row["value"].is_finite() and row["value"] >= 0:
            grouped[key[-1]].append(row)
    return {code: {"code": code, "label": label, "value": sum((r["value"] for r in grouped[code]), Decimal(0)) if grouped[code] else None,
                   "objects": len(grouped[code]), "observed_at": max((r["observed_at"] for r in grouped[code]), default=None)}
            for code, label in LABELS.items()}


def _history(rows, current, platform):
    expected = {key for key, row in current.items() if key[0] == platform and key[-1] == "followers"
                and row["value"] is not None and row["value"].is_finite() and row["value"] >= 0}
    if not expected:
        return [], "", None
    by_day = defaultdict(dict)
    for row in rows:
        code = _measurement(row)
        if code != "followers" or row["value"] is None or not row["value"].is_finite() or row["value"] < 0:
            continue
        key = _key(row, code)
        if key not in expected:
            continue
        daily = by_day[row["period_start"].date()]
        if key not in daily or _rank(row) > _rank(daily[key]):
            daily[key] = row
    # A portfolio history point requires the same profile coverage as its current value.
    history = [{"day": day, "value": sum((r["value"] for r in values.values()), Decimal(0))}
               for day, values in sorted(by_day.items()) if set(values) == expected]
    points, delta = "", None
    if len(history) > 1:
        low, high = min(r["value"] for r in history), max(r["value"] for r in history)
        span = (history[-1]["day"] - history[0]["day"]).days or 1
        points = " ".join(f'{(r["day"] - history[0]["day"]).days * 260 / span:.1f},{54 - float((r["value"] - low) / (high - low or 1)) * 48:.1f}' for r in history)
        delta = history[-1]["value"] - history[0]["value"]
    return history, points, delta


def dashboard_for(user, artist, *, portfolio=False):
    portfolio = bool(portfolio and user.is_staff and user.is_active)
    available = artists_for(user)
    artists = list(collection_artists().filter(pk__in=available.values("pk"))) if portfolio else list(available.filter(pk=artist.pk))
    scope = Q(pk__in=[])
    run_scope = Q(pk__in=[])
    for item in artists:
        scope |= Q(organization_id=item.organization_id, sync_run__sync_job__connection__artist=item,
                   sync_run__sync_job__connection__organization_id=item.organization_id)
        run_scope |= Q(sync_job__connection__artist=item, sync_job__connection__organization_id=item.organization_id)
    sources = SourceRecord.objects.filter(scope, sync_run__sync_job__job_type="daily_public_platform",
        service__slug__in=PLATFORMS, collector__in=["omkar", "spotify-web-api"],
        service_id=F("sync_run__sync_job__connection__service_id"))
    identity = Q(pk__in=[])
    for endpoint, kind in KINDS.items():
        identity |= Q(source_record__object_type=endpoint, series__platform_object__object_type=kind)
    observations = MetricObservation.objects.filter(source_record__in=sources, quality_status__in=["pending", "accepted"],
        series__organization_id=F("source_record__organization_id"),
        series__platform_object__service_id=F("source_record__service_id"),
        series__platform_object__external_id=F("source_record__external_id"),
        series__metric_definition__service_id=F("source_record__service_id"), series__metric_definition__unit="count")
    observations = observations.filter(identity)
    current_rows = list(observations.order_by("series_id", ARTIST_FIELD, "-period_start", "-revision", "-observed_at", "-id").distinct("series_id", ARTIST_FIELD).values(*FIELDS))
    daily_rows = list(observations.filter(period_start__gte=timezone.now() - timedelta(days=30)).order_by(
        "series_id", ARTIST_FIELD, "period_start", "-revision", "-observed_at", "-id").distinct("series_id", ARTIST_FIELD, "period_start").values(*FIELDS))
    current = _current(current_rows)
    totals = _totals(current)
    metadata = list(sources.filter(object_type__in=KINDS).values("service__slug", "object_type", "external_id").annotate(last=Max("observed_at")))
    latest_runs = list(SyncRun.objects.filter(run_scope, sync_job__job_type="daily_public_platform").select_related(
        "sync_job__connection__artist", "sync_job__connection__service").order_by(
        "sync_job__connection__artist_id", "sync_job__connection__service_id", "-created_at", "-id").distinct(
        "sync_job__connection__artist_id", "sync_job__connection__service_id"))
    platforms = []
    for slug, name in PLATFORMS.items():
        metrics = _totals({key: value for key, value in current.items() if key[0] == slug})
        items = [r for r in metadata if r["service__slug"] == slug]
        latest = max((r["last"] for r in items), default=None)
        runs = [run for run in latest_runs if run.sync_job.connection.service.slug == slug]
        limited = any(r.counts_json.get("provider_limits") for r in runs)
        state = "Incomplete" if any(r.status == "failed" for r in runs) else "Collecting" if any(r.status == "running" for r in runs) else "Provider limited" if limited else "Updated" if runs and all(r.status == "succeeded" for r in runs) else "Not collected"
        history, points, delta = _history(daily_rows, current, slug)
        platforms.append({"slug": slug, "name": name, "logo_url": brand_logo(slug, "social") or brand_logo(slug),
            "metrics": metrics, "display_metrics": [v for v in metrics.values() if v["value"] is not None],
            "items": len({(KINDS[r["object_type"]], r["external_id"]) for r in items}),
            "latest": latest, "stale": bool(latest and latest < timezone.now() - timedelta(hours=48)), "state": state,
            "history": history, "points": points, "delta": delta, "limited": limited,
            "insights_url": reverse("portal:insights", args=[artist.pk]) + "?platform=" + slug})
    artist_summaries = []
    if portfolio:
        for item in artists:
            values = _totals(_current([r for r in current_rows if r[ARTIST_FIELD] == item.pk]))
            artist_summaries.append({"artist": item, "metrics": [values[k] for k in HEADLINES],
                "url": reverse("portal:overview") + f"?artist={item.pk}",
                "insights_url": reverse("portal:insights", args=[item.pk])})
    return {"portfolio": portfolio, "artist_count": len(artists), "artist_summaries": artist_summaries,
        "headline": {k: totals[k] for k in HEADLINES}, "headline_cards": [totals[k] for k in HEADLINES],
        "platforms": platforms, "has_data": any(r["value"] is not None for r in totals.values()),
        "latest": max((r["last"] for r in metadata), default=None), "has_incomplete": any(r.status == "failed" for r in latest_runs),
        "all_url": reverse("portal:overview") + f"?artist={artist.pk}&metrics_scope=all",
        "artist_url": reverse("portal:overview") + f"?artist={artist.pk}"}
