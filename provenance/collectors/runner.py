"""Daily collection with a PostgreSQL process lock and immutable source snapshots."""
import hashlib
import json
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Max, Q
from django.utils import timezone

from analytics.models import MetricDefinition, MetricObservation, MetricSeries, dimension_hash
from artists.models import ArtistProfileLink
from artists.customer_workspaces import collection_artists
from catalog.models import PlatformObject, RecordingPlatformMatch
from provenance.models import EntityTarget, ReviewCandidate, ReviewItem, SourceRecord, SyncJob, SyncRun
from provenance.validation import SECRET_KEYS
from .http import Budget, CollectionError, ENDPOINT_NAMES, safe_error_code
from .providers import PLATFORMS, REUSABLE_ENDPOINTS, SnapshotCache, collect, profile_locator

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


class MetricWriter:
    """Invocation-local lookups; never reuse state after a rolled-back job."""
    def __init__(self):
        self.definitions = {}
        self.series = {}
        self.revisions = {}


def record_metrics(source, obj, endpoint, body, day, *, writer=None):
    writer = writer or MetricWriter()
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
    pending = []
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
            definition_key = (obj.service_id, code)
            definition = writer.definitions.get(definition_key)
            if definition is None:
                definition, _ = MetricDefinition.objects.get_or_create(service=obj.service, code=code, definition_version=1, defaults={
                    "unit": "rank" if code == "world_rank" else "count",
                    "aggregation_rule": "rolling" if code in {"monthly_listeners", "city_listeners"} else "cumulative" if code in {"playcount", "plays", "view_count", "play_count", "downloads"} else "gauge", "period_type": "daily_snapshot",
                })
                writer.definitions[definition_key] = definition
            series_key = (source.organization_id, definition.pk, obj.pk, dimension_hash(dims))
            series = writer.series.get(series_key)
            if series is None:
                series, _ = MetricSeries.objects.get_or_create(
                    organization=source.organization, metric_definition=definition, platform_object=obj,
                    dimensions_hash=series_key[-1], source_method="omkar", defaults={"dimensions_json": dims},
                )
                writer.series[series_key] = series
            pending.append((series, value))
    if not pending:
        return
    end = day + timedelta(days=1)
    missing = {series.pk for series, _ in pending if (series.pk, day) not in writer.revisions}
    previous = dict(MetricObservation.objects.filter(series_id__in=missing, period_start=day, period_end=end)
                    .values("series_id").annotate(revision=Max("revision")).values_list("series_id", "revision")) if missing else {}
    observations, revisions = [], {}
    for series, value in pending:
        key = (series.pk, day)
        revision = revisions.get(key, writer.revisions.get(key, previous.get(series.pk, 0))) + 1
        observation = MetricObservation(series=series, period_start=day, period_end=end,
            observed_at=source.observed_at, value=value, source_record=source,
            revision=revision, quality_status="pending")
        # bulk_create skips save(); explicitly retain every field, relationship,
        # uniqueness and constraint validation before the atomic insert.
        observation.full_clean()
        observations.append(observation)
        revisions[key] = revision
    MetricObservation.objects.bulk_create(observations)
    writer.revisions.update(revisions)


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
    key = (collector, row.endpoint, row.external_id)
    hashes = getattr(run, "_latest_hashes", None)
    if hashes is None:
        source = SourceRecord.objects.filter(
            sync_run__sync_job=run.sync_job, collector=collector, object_type=row.endpoint,
            external_id=row.external_id,
            observed_at__gte=day, observed_at__lt=day + timedelta(days=1),
        ).order_by("-observed_at", "-created_at", "-id").only("payload_hash").first()
        previous_hash = source.payload_hash if source else None
    else:
        previous_hash = hashes.get((day, *key))
    if previous_hash == digest:
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
        record_metrics(source, obj, row.endpoint, payload, day, writer=getattr(run, "_metric_writer", None))
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
    if hashes is not None:
        hashes[(day, *key)] = digest
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


def same_day_snapshots(job, now):
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return SourceRecord.objects.filter(
        sync_run__sync_job=job, organization=job.connection.organization,
        service=job.connection.service, observed_at__gte=day,
        observed_at__lt=day + timedelta(days=1), parser_version="omkar-public-v1",
    ).order_by("collector", "object_type", "external_id", "-observed_at", "-created_at", "-id").distinct("collector", "object_type", "external_id")


def profile_fingerprint(platform, url):
    locator = profile_locator(platform, url)
    if platform == "apple-music":
        # Song details vary by storefront even when the artist ID is unchanged.
        country = urlsplit(url).path.strip("/").split("/")[0].lower()
        locator = f"{country}:{locator}"
    return hashlib.sha256(f"{platform}\0{locator}".encode()).hexdigest()


def retry_snapshots(job, now, force, *, fingerprint=None):
    cache = SnapshotCache()
    cache.day = now.date()
    if fingerprint is None:
        link = job_profile(job)
        if not link:
            return cache
        fingerprint = profile_fingerprint(job.connection.service.slug, link.url)
    previous = SyncRun.objects.filter(sync_job=job).order_by("-started_at", "-created_at", "-id").first()
    if force or not previous or previous.status != "failed" or previous.counts_json.get("profile_fingerprint") != fingerprint:
        return cache
    for endpoint, external_id, payload in same_day_snapshots(job, now).filter(
        object_type__in=REUSABLE_ENDPOINTS, sync_run__counts_json__profile_fingerprint=fingerprint,
    ).values_list("object_type", "external_id", "payload_json"):
        if isinstance(payload, dict) and not payload.get("error") and not payload.get("error_code"):
            cache[(endpoint, external_id)] = payload
    return cache


def run_due(*, request_limit=1000, job_limit=250, artist=None, force=False, progress=None):
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
            started = timezone.now()
            SyncRun.objects.filter(sync_job=job, status="running").update(status="failed", finished_at=started, error_summary="interrupted")
            fingerprint = profile_fingerprint(job.connection.service.slug, link.url)
            reuse = retry_snapshots(job, started, force, fingerprint=fingerprint)
            day = started.replace(hour=0, minute=0, second=0, microsecond=0)
            latest_hashes = {(day, collector, endpoint, external_id): digest for collector, endpoint, external_id, digest in
                same_day_snapshots(job, started).values_list("collector", "object_type", "external_id", "payload_hash")}
            run = SyncRun.objects.create(sync_job=job, status="running", started_at=started, counts_json={"profile_fingerprint": fingerprint})
            run._metric_writer = MetricWriter()
            run._latest_hashes = latest_hashes
            count, incomplete, error, rows_seen = 0, False, "", 0
            endpoint_errors = {}
            before = budget.used
            checkpoint_at = time.monotonic()

            def counts():
                return {"profile_fingerprint": fingerprint, "snapshots_created": count, "snapshots_reused": reuse.hits,
                        "rows_seen": rows_seen, "requests": budget.used - before,
                        "incomplete": incomplete, "endpoint_errors": {endpoint: errors.copy() for endpoint, errors in endpoint_errors.items()}}

            def emit(event, endpoint=""):
                if progress:
                    progress_counts = counts()
                    progress_counts.pop("profile_fingerprint")
                    progress({"event": event, "job_id": str(job.pk), "run_id": str(run.pk),
                              "platform": job.connection.service.slug, "status": run.status,
                              "endpoint": endpoint, "error_code": error, **progress_counts})

            emit("job_started")
            try:
                for row in collect(job.connection.service.slug, link.url, budget, reuse=reuse):
                    observed_at = timezone.now()
                    row_day = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
                    if row_day != day:
                        # A long invocation may cross midnight: old hashes cannot
                        # suppress the next day's evidence or metric observations.
                        run._latest_hashes = None
                    count += persist(run, row, observed_at)
                    rows_seen += 1
                    incomplete |= row.incomplete
                    if row.incomplete:
                        endpoint = row.endpoint.removesuffix("-unavailable")
                        code = safe_error_code(CollectionError(row.payload.get("error_code", "provider_collection_truncated")))
                        errors = endpoint_errors.setdefault(endpoint, {})
                        errors[code] = errors.get(code, 0) + 1
                    elapsed = time.monotonic() - checkpoint_at
                    if rows_seen == 1 or row.incomplete or rows_seen % 25 == 0 or elapsed >= 15:
                        run.counts_json = counts()
                        run.save(update_fields=["counts_json", "updated_at"])
                        emit("job_progress", row.endpoint)
                        checkpoint_at = time.monotonic()
                if incomplete:
                    raise CollectionError("provider_collection_truncated")
            except CollectionError as exc:
                incomplete = True
                error = safe_error_code(exc)
                endpoint = getattr(exc, "endpoint", "")
                if endpoint in ENDPOINT_NAMES:
                    errors = endpoint_errors.setdefault(endpoint, {})
                    errors[error] = errors.get(error, 0) + 1
            except Exception:
                incomplete = True
                error = "unexpected_collection_error"
            run.status = "failed" if error else "succeeded"
            run.finished_at = timezone.now()
            run.counts_json = counts()
            run.error_summary = error
            run.save(update_fields=["status", "finished_at", "counts_json", "error_summary", "updated_at"])
            if not error:
                job.next_run_at = run.finished_at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                job.save(update_fields=["next_run_at", "updated_at"])
            emit("job_finished")
            summary["failed" if error else "succeeded"] += 1
            summary["snapshots"] += count
        summary["requests"] = budget.used
        return summary
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
