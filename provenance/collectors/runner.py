"""Daily collection with a PostgreSQL process lock and immutable source snapshots."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from analytics.models import MetricDefinition, MetricObservation, MetricSeries, dimension_hash
from artists.models import ArtistProfileLink
from artists.customer_workspaces import collection_artists
from catalog.models import PlatformObject, RecordingPlatformMatch
from provenance.models import EntityTarget, ReviewCandidate, ReviewItem, SourceRecord, SyncJob, SyncRun
from provenance.validation import SECRET_KEYS
from .http import Budget, CollectionError
from .providers import PLATFORMS, collect, profile_locator

JOB_TYPE = "daily_public_platform"
LOCK_ID = 1837012026


def eligible_profiles():
    from portal.querysets import active_rows
    return active_rows(ArtistProfileLink.objects.filter(
        verification_status="verified", artist__in=collection_artists(), platform__slug__in=PLATFORMS,
    )).select_related("artist", "platform")


def safe_payload(value):
    """Providers can return credential-shaped fields, including SoundCloud tokens."""
    if isinstance(value, dict):
        return {k: safe_payload(v) for k, v in value.items()
                if str(k).lower().replace("-", "_") not in SECRET_KEYS | {"secret_token", "api_token", "token", "set_cookie"}}
    if isinstance(value, list):
        return [safe_payload(v) for v in value]
    return value


def record_metrics(source, obj, endpoint, body, day):
    if endpoint == "spotify-track":
        return  # Official catalog metadata is separate from Omkar audience metrics.
    if endpoint == "channel-details":
        body = body.get("channel", {})
    elif endpoint == "video-details":
        body = body.get("video", {})
    stats = body.get("stats") or {}
    values = {key: stats.get(key, body.get(key)) for key in (
        "followers", "monthly_listeners", "world_rank", "plays", "likes", "reposts",
        "comments", "downloads", "tracks", "playlists", "playcount", "subscriber_count",
        "video_count", "view_count", "like_count", "comment_count", "follower_count",
        "following_count", "post_count", "play_count", "repost_count",
    )}
    dimensions = [({"scope": "public"}, values)]
    for city in stats.get("top_cities") or []:
        if isinstance(city, dict):
            dimensions.append(({"scope": "public", **{k: city.get(k) for k in ("city", "region", "country")}}, {"city_listeners": city.get("listeners")}))
    for dims, metrics in dimensions:
        for code, raw in metrics.items():
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                continue
            if not value.is_finite() or value < 0 or value >= 10 ** 18:
                continue
            definition, _ = MetricDefinition.objects.get_or_create(service=obj.service, code=code, definition_version=1, defaults={
                "unit": "rank" if code == "world_rank" else "count",
                "aggregation_rule": "rolling" if code in {"monthly_listeners", "city_listeners"} else "cumulative" if code in {"playcount", "plays", "view_count", "play_count", "downloads"} else "gauge", "period_type": "daily_snapshot",
            })
            series, _ = MetricSeries.objects.get_or_create(
                organization=source.organization, metric_definition=definition, platform_object=obj,
                dimensions_hash=dimension_hash(dims),
                source_method="omkar", defaults={"dimensions_json": dims},
            )
            previous = MetricObservation.objects.filter(series=series, period_start=day, period_end=day + timedelta(days=1)).order_by("-revision").first()
            MetricObservation.objects.create(series=series, period_start=day, period_end=day + timedelta(days=1),
                observed_at=source.observed_at, value=value, source_record=source,
                revision=previous.revision + 1 if previous else 1, quality_status="pending")


@transaction.atomic
def persist(run, row, observed_at):
    conn = run.sync_job.connection
    payload = safe_payload(row.payload)
    if not isinstance(payload, dict):
        raise CollectionError("invalid_response_shape")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    day = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    collector = "spotify-web-api" if row.endpoint == "spotify-track" else "omkar"
    # Same-day retries reuse evidence even if a prior run failed part-way through.
    source = SourceRecord.objects.filter(
        sync_run__sync_job=run.sync_job, collector=collector, object_type=row.endpoint,
        external_id=row.external_id,
        observed_at__gte=day, observed_at__lt=day + timedelta(days=1),
    ).order_by("-observed_at", "-created_at", "-id").first()
    if source and source.payload_hash == digest:
        return False
    source = SourceRecord.objects.create(
        organization=conn.organization, sync_run=run, service=conn.service,
        collector=collector, collection_method="api" if collector == "spotify-web-api" else "scraper",
        parser_version="omkar-public-v1", object_type=row.endpoint, external_id=row.external_id,
        payload_hash=digest, payload_json=payload, observed_at=observed_at,
    )
    if row.kind != "collection":
        body = payload.get("channel" if row.kind == "channel" else "video", payload) if row.kind in {"channel", "video"} else payload
        obj, created = PlatformObject.objects.get_or_create(service=conn.service, object_type=row.kind,
            external_id=row.external_id, defaults={"url": (body.get("link") or body.get("external_urls", {}).get("spotify") or "")[:2048],
                "title": (body.get("name") or body.get("title") or "")[:300], "first_seen_at": observed_at})
        PlatformObject.objects.filter(pk=obj.pk).update(last_seen_at=observed_at)
        record_metrics(source, obj, row.endpoint, payload, day)
        endpoint_types = {"track": ["track-details", "song-details", "spotify-track"], "video": ["video-details"], "post": ["post-details"]}.get(row.kind)
        matched = RecordingPlatformMatch.objects.filter(platform_object=obj,
            recording__organization=conn.organization, recording__artist_grants__artist=conn.artist,
            recording__archived_at__isnull=True, review_status="accepted").exists() if endpoint_types else False
        reviewed = ReviewCandidate.objects.filter(target__artist=conn.artist,
            review_item__issue_type="platform_object_discovered", review_item__source_record__service=conn.service,
            review_item__source_record__external_id=row.external_id,
            review_item__source_record__object_type__in=endpoint_types).exists() if endpoint_types else False
        if endpoint_types and not matched and not reviewed:
            target, _ = EntityTarget.objects.get_or_create(organization=conn.organization, artist=conn.artist)
            review = ReviewItem.objects.create(source_record=source, issue_type="platform_object_discovered")
            ReviewCandidate.objects.create(review_item=review, target=target)
    return True


def job_profile(job):
    conn = job.connection
    if conn.status != "active" or not conn.artist_id or conn.organization_id != conn.artist.organization_id or conn.service.slug not in PLATFORMS:
        return None
    try:
        link = eligible_profiles().filter(pk=job.cursor_json.get("profile_link_id"), artist=conn.artist, platform=conn.service).first()
    except (ValidationError, ValueError, TypeError):
        return None
    if link:
        profile_locator(conn.service.slug, link.url)
    return link


def run_due(*, request_limit=1000, job_limit=250, artist=None, force=False):
    if not 1 <= job_limit <= 10000:
        raise CollectionError("invalid_job_limit")
    budget = Budget(request_limit)
    summary = {"succeeded": 0, "failed": 0, "skipped": 0, "snapshots": 0, "requests": 0, "locked": False}
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
        acquired = cursor.fetchone()[0]
    if not acquired:
        summary["locked"] = True
        return summary
    try:
        now = timezone.now()
        jobs = SyncJob.objects.filter(job_type=JOB_TYPE, enabled=True).select_related("connection__artist", "connection__service", "connection__organization")
        if not force:
            jobs = jobs.filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
        if artist:
            jobs = jobs.filter(connection__artist_id=artist)
        for job in jobs.order_by("next_run_at", "id")[:job_limit]:
            try:
                link = job_profile(job)
            except (CollectionError, ValueError):
                link = None
            if not link:
                summary["skipped"] += 1
                continue
            # A killed container releases its advisory lock. Keep an honest failed
            # receipt for any run it left behind before attempting a new run.
            SyncRun.objects.filter(sync_job=job, status="running").update(status="failed", finished_at=now, error_summary="interrupted")
            run = SyncRun.objects.create(sync_job=job, status="running", started_at=now)
            count, incomplete, error = 0, False, ""
            before = budget.used
            try:
                for row in collect(job.connection.service.slug, link.url, budget):
                    count += persist(run, row, now)
                    incomplete |= row.incomplete
                if incomplete:
                    raise CollectionError("provider_collection_truncated")
            except CollectionError as exc:
                # Only our fixed codes are safe to retain; never HTTP bodies/URLs.
                error = str(exc) if str(exc).replace("_", "").isalnum() else "collection_error"
            except Exception:
                error = "unexpected_collection_error"
            run.status = "failed" if error else "succeeded"
            run.finished_at = timezone.now()
            run.counts_json = {"snapshots_created": count, "requests": budget.used - before, "incomplete": incomplete}
            run.error_summary = error
            run.save(update_fields=["status", "finished_at", "counts_json", "error_summary", "updated_at"])
            if not error:
                job.next_run_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                job.save(update_fields=["next_run_at", "updated_at"])
            summary["failed" if error else "succeeded"] += 1
            summary["snapshots"] += count
        summary["requests"] = budget.used
        return summary
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
