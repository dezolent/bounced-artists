"""Daily collection with a PostgreSQL process lock and immutable source snapshots."""
import hashlib
import json
import os
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
from provenance.models import AuditEvent, EntityTarget, ReviewCandidate, ReviewItem, SourceRecord, SyncJob, SyncRun
from provenance.validation import SECRET_KEYS
from .http import Budget, CollectionError, ENDPOINT_NAMES, safe_error_code
from .backends import scraper_backend
from .providers import PLATFORMS, REUSABLE_ENDPOINTS, SnapshotCache, collect, profile_locator
from . import catalog as catalog_discovery
from .catalog_services import enrichment_platform

JOB_TYPE = "daily_public_platform"
LOCK_ID = 1837012026

STREAMING_SERVICES = ("spotify", "spotify-audio-features", "apple-music", "soundcloud")
SOCIAL_SERVICES = ("youtube", "instagram", "tiktok")


def streaming_service_jobs(jobs):
    """Order all due streaming jobs before any social collection."""
    return sorted((job for job in jobs if enrichment_platform(job) in STREAMING_SERVICES),
                  key=lambda job: (STREAMING_SERVICES.index(enrichment_platform(job)),
                                   getattr(job.connection.artist, "bootstrap_key", "") != "dezolent", str(job.pk)))


def social_media_jobs(jobs):
    """Collect YouTube first, then Instagram and TikTok."""
    return sorted((job for job in jobs if enrichment_platform(job) in SOCIAL_SERVICES),
                  key=lambda job: (SOCIAL_SERVICES.index(enrichment_platform(job)), str(job.pk)))


def run_streaming_services(jobs, run_job):
    """Run all selected streaming jobs before considering social jobs."""
    return all(run_job(job) for job in jobs)


def run_social_media(jobs, run_job):
    """Run the selected social jobs in priority order."""
    return all(run_job(job) for job in jobs)


def reserve_audio_request(organization_id):
    """Reserve a billable attempt before I/O; use a rolling day as a safe ceiling."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [1837012027])
        if AuditEvent.objects.filter(action='audio_features.request_reserved', created_at__gte=timezone.now() - timedelta(hours=24)).count() >= 10:
            return False
        AuditEvent.objects.create(organization_id=organization_id, action='audio_features.request_reserved', target_label='Audio feature request', change_json={'provider': 'musicae'})
        return True


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
    if endpoint in {"spotify-track", "spotify-audio-features"}:
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
        "share_count", "save_count", "friend_count", "liked_video_count",
    )}
    if obj.service.slug == "tiktok" and obj.object_type in {"video", "usage_video"}:
        # TikTok calls its view counter play_count. Keep the native payload as
        # evidence, but normalize the observed counter for cross-platform views.
        values["view_count"] = values.pop("play_count")
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
    if row.endpoint == "spotify-audio-features":
        from .audio_features import validate_feature_row
        if not validate_feature_row(run, row):
            return False
    payload = safe_payload(row.payload)
    if not isinstance(payload, dict):
        raise CollectionError("invalid_response_shape")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    day = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    collector = "musicae-rapidapi" if row.endpoint.startswith("spotify-audio-features") else "spotify-web-api" if row.endpoint == "spotify-track" else "omkar"
    scope = {}
    fingerprint = run.counts_json.get("profile_fingerprint")
    if fingerprint:
        scope["sync_run__counts_json__profile_fingerprint"] = fingerprint
    # Same-day retries reuse evidence only within the same backend/profile identity.
    key = (collector, row.endpoint, row.external_id)
    hashes = getattr(run, "_latest_hashes", None)
    if hashes is None:
        source = SourceRecord.objects.filter(
            sync_run__sync_job=run.sync_job, collector=collector, object_type=row.endpoint,
            external_id=row.external_id, **scope,
            observed_at__gte=day, observed_at__lt=day + timedelta(days=1),
        ).order_by("-observed_at", "-created_at", "-id").only("payload_hash").first()
        previous_hash = source.payload_hash if source else None
    else:
        previous_hash = hashes.get((day, *key))
    new_snapshot = previous_hash != digest
    if not new_snapshot and row.kind not in {"track", "music"}:
        return False
    source = SourceRecord.objects.filter(sync_run__sync_job=run.sync_job, collector=collector,
        object_type=row.endpoint, external_id=row.external_id, payload_hash=digest, **scope,
        observed_at__gte=day, observed_at__lt=day + timedelta(days=1)).order_by("-observed_at", "-id").first() if not new_snapshot else None
    source = source or SourceRecord.objects.create(
        organization=conn.organization, sync_run=run, service=conn.service,
        collector=collector, collection_method="api" if collector in {"spotify-web-api", "musicae-rapidapi"} else "scraper",
        parser_version="spotify-audio-features-v1" if row.endpoint.startswith("spotify-audio-features") else "omkar-public-v1", object_type=row.endpoint, external_id=row.external_id,
        payload_hash=digest, payload_json=payload, observed_at=observed_at,
    )
    if row.kind != "collection" and row.endpoint != "spotify-audio-features":
        body = payload.get("channel", payload) if row.kind == "channel" else payload.get("video", payload) if row.endpoint == "video-details" else payload
        if conn.service.slug == "musixmatch":
            body = payload.get("track") if isinstance(payload.get("track"), dict) else payload
        obj, created = PlatformObject.objects.get_or_create(service=conn.service, object_type=row.kind,
            external_id=row.external_id, defaults={"url": (body.get("link") or body.get("external_urls", {}).get("spotify") or "")[:2048],
                "title": (body.get("name") or body.get("title") or "")[:300], "first_seen_at": observed_at})
        PlatformObject.objects.filter(pk=obj.pk).update(last_seen_at=observed_at)
        if catalog_discovery.ensure_catalog_recording(conn, obj, source, row):
            run._catalog_created = getattr(run, "_catalog_created", 0) + 1
        if row.endpoint == "tiktok-music":
            from .tiktok_catalog import attach_music_matches
            attach_music_matches(conn, obj, source, row)
        if conn.service.slug == 'youtube' and row.endpoint == 'video-details':
            from .youtube_catalog import attach_youtube_match
            if attach_youtube_match(conn, obj, source, row):
                run._youtube_matched = getattr(run, '_youtube_matched', 0) + 1
        if conn.service.slug == "musixmatch":
            from .catalog_services import attach_catalog_match
            attach_catalog_match(conn, obj, source, row)
            if conn.service.slug == "musixmatch":
                from .lyrics import import_lyrics
                if import_lyrics(conn, obj, source, row):
                    run._lyrics_created = getattr(run, "_lyrics_created", 0) + 1
        if new_snapshot:
            record_metrics(source, obj, row.endpoint, payload, day, writer=getattr(run, "_metric_writer", None))
        endpoint_types = {"track": ["track-details", "song-details", "spotify-track"], "video": ["video-details"], "post": ["post-details", "post-list-item"], "music": ["tiktok-music"]}.get(row.kind)
        # Social posts/videos are observations, not candidate song identities.
        # Preserve their evidence and metrics without requiring catalog review.
        if conn.service.slug in {"tiktok", "instagram"} and row.kind in {"video", "post"}:
            endpoint_types = None
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
    return new_snapshot


def job_profile(job):
    conn = job.connection
    if enrichment_platform(job) in {"spotify-audio-features", "musixmatch"}:
        from .catalog_services import seed_profile
        return seed_profile(job)
    if conn.status != "active" or not conn.artist_id or conn.organization_id != conn.artist.organization_id or conn.service.slug not in PLATFORMS:
        return None
    try:
        link = eligible_profiles().filter(pk=job.cursor_json.get("profile_link_id"), artist=conn.artist, platform=conn.service).first()
    except (ValidationError, ValueError, TypeError):
        return None
    if link:
        profile_locator(conn.service.slug, link.url)
    return link


def same_day_snapshots(job, now, *, fingerprint=None):
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    scope = {"sync_run__counts_json__profile_fingerprint":fingerprint} if fingerprint else {}
    return SourceRecord.objects.filter(
        sync_run__sync_job=job, organization=job.connection.organization, **scope,
        service=job.connection.service, observed_at__gte=day,
        observed_at__lt=day + timedelta(days=1), parser_version__in={"omkar-public-v1", "spotify-audio-features-v1"},
    ).order_by("collector", "object_type", "external_id", "-observed_at", "-created_at", "-id").distinct("collector", "object_type", "external_id")


def profile_fingerprint(platform, url):
    locator = profile_locator({"spotify-audio-features":"spotify", "musixmatch":"spotify"}.get(platform,platform), url)
    if platform == "apple-music":
        # Song details vary by storefront even when the artist ID is unchanged.
        country = urlsplit(url).path.strip("/").split("/")[0].lower()
        locator = f"{country}:{locator}"
    backend = scraper_backend(platform)
    suffix = "" if backend.mode == "hosted" else f"\0{backend.identity}"
    return hashlib.sha256(f"{platform}\0{locator}{suffix}".encode()).hexdigest()


def retry_snapshots(job, now, force, *, fingerprint=None):
    cache = SnapshotCache()
    cache.day = now.date()
    if fingerprint is None:
        link = job_profile(job)
        if not link:
            return cache
        fingerprint = profile_fingerprint(enrichment_platform(job), link.url)
    previous = SyncRun.objects.filter(sync_job=job).order_by("-started_at", "-created_at", "-id").first()
    if force or not previous or previous.status != "failed" or previous.counts_json.get("profile_fingerprint") != fingerprint:
        return cache
    for endpoint, external_id, payload in same_day_snapshots(job, now).filter(
        object_type__in=REUSABLE_ENDPOINTS, sync_run__counts_json__profile_fingerprint=fingerprint,
    ).values_list("object_type", "external_id", "payload_json"):
        if isinstance(payload, dict) and not payload.get("error") and not payload.get("error_code"):
            if endpoint == "tiktok-video":
                metadata = payload.get("_collection")
                if not isinstance(metadata, dict) or metadata.get("endpoint") != "tiktok-videos-details":
                    continue  # List evidence must never satisfy a failed detail fetch.
            if endpoint == "post-details":
                # Requests use the shortcode; SourceRecord keeps the numeric ID.
                external_id = payload.get("code")
                if not isinstance(external_id, str):
                    continue
            cache[(endpoint, external_id)] = payload
    return cache


def run_due(*, request_limit=18000, job_request_limit=1000, job_limit=250, artist=None, force=False, progress=None, time_limit=7200, job_time_limit=300):
    if not 1 <= job_limit <= 10000:
        raise CollectionError("invalid_job_limit")
    total_budget = Budget(request_limit, seconds=time_limit)
    Budget(job_request_limit, seconds=job_time_limit)  # Validate before creating any run receipts.
    summary = {"succeeded": 0, "failed": 0, "skipped": 0, "snapshots": 0, "requests": 0, "locked": False, "social_blocked": False}
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
        acquired = cursor.fetchone()[0]
    if not acquired:
        summary["locked"] = True
        return summary
    try:
        now = timezone.now()
        feature_access = {}  # One app-level 403 is shared across this invocation's artist jobs.
        jobs = SyncJob.objects.filter(job_type=JOB_TYPE, enabled=True).select_related("connection__artist", "connection__service", "connection__organization")
        if not force:
            jobs = jobs.filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
        if artist:
            jobs = jobs.filter(connection__artist_id=artist)
        jobs = list(jobs)
        streaming = streaming_service_jobs(jobs)
        social = social_media_jobs(jobs)
        ordered = streaming + social
        if len(ordered) > job_limit:
            summary["job_limit_reached"] = True
            if len(streaming) > job_limit:
                summary["social_blocked"] = True
        def run_job(job):
            try:
                total_budget.check_time()
            except CollectionError:
                summary["time_limit_reached"] = True
                return False
            budget = Budget(job_request_limit, parent=total_budget, seconds=job_time_limit)
            try:
                link = job_profile(job)
            except (CollectionError, ValueError, TypeError, AttributeError):
                link = None
            if not link:
                summary["skipped"] += 1
                return True
            started = timezone.now()
            SyncRun.objects.filter(sync_job=job, status="running").update(status="failed", finished_at=started, error_summary="interrupted")
            fingerprint = profile_fingerprint(enrichment_platform(job), link.url)
            reuse = retry_snapshots(job, started, force, fingerprint=fingerprint)
            day = started.replace(hour=0, minute=0, second=0, microsecond=0)
            latest_hashes = {(day, collector, endpoint, external_id): digest for collector, endpoint, external_id, digest in
                same_day_snapshots(job, started, fingerprint=fingerprint).values_list("collector", "object_type", "external_id", "payload_hash")}
            backend = scraper_backend(enrichment_platform(job))
            run = SyncRun.objects.create(sync_job=job, status="running", started_at=started, counts_json={"profile_fingerprint": fingerprint, "scraper_backend":backend.mode, "backend_identity":backend.identity, "scraper_revision":backend.revision})
            run._metric_writer = MetricWriter()
            run._latest_hashes = latest_hashes
            count, incomplete, error, rows_seen = 0, False, "", 0
            seed_count = None
            seed_cursor = ""
            seeds_processed = 0
            endpoint_errors = {}
            provider_limits = {}
            before = budget.used
            job_started_at = time.monotonic()
            persist_seconds = 0.0
            checkpoint_at = time.monotonic()

            def counts():
                return {"profile_fingerprint": fingerprint, "scraper_backend":backend.mode, "backend_identity":backend.identity, "scraper_revision":backend.revision, "catalog_seed_count":seed_count, "catalog_seed_cursor":seed_cursor, "catalog_seeds_processed":seeds_processed, "snapshots_created": count, "snapshots_reused": reuse.hits,
                        "rows_seen": rows_seen, "requests": budget.used - before,
                        "incomplete": incomplete, "provider_limits": provider_limits.copy(),
                        "catalog_recordings_created": getattr(run, "_catalog_created", 0),
                        "youtube_recordings_matched": getattr(run, "_youtube_matched", 0),
                        "lyrics_candidates_created": getattr(run, "_lyrics_created", 0),
                        "elapsed_seconds": round(time.monotonic() - job_started_at, 3),
                        "persist_seconds": round(persist_seconds, 3),
                        "endpoint_errors": {endpoint: errors.copy() for endpoint, errors in endpoint_errors.items()}}

            def emit(event, endpoint=""):
                if progress:
                    progress_counts = counts()
                    progress_counts.pop("profile_fingerprint")
                    progress({"event": event, "job_id": str(job.pk), "run_id": str(run.pk),
                              "platform": enrichment_platform(job), "status": run.status,
                              "phase": "streaming" if enrichment_platform(job) in STREAMING_SERVICES else "social",
                              "endpoint": endpoint, "error_code": error, **progress_counts})

            emit("job_started")
            try:
                unavailable = set(os.environ.get('ARTIST_SCRAPER_UNAVAILABLE', '').split(','))
                if enrichment_platform(job).replace('-', '_') in unavailable:
                    raise CollectionError('provider_startup_unavailable')
                options = {"reuse": reuse}
                if enrichment_platform(job) == "spotify-audio-features":
                    options["feature_access"] = feature_access
                    options["reserve_request"] = lambda: reserve_audio_request(job.connection.organization_id)
                if job.connection.service.slug == "tiktok":
                    from .tiktok_catalog import music_context
                    options["music_ids"], options["is_artist_music"] = music_context(job)
                if enrichment_platform(job) in {"spotify-audio-features", "musixmatch"}:
                    from .catalog_services import catalog_seeds
                    options["catalog_seeds"] = catalog_seeds(job)
                    seed_count = len(options["catalog_seeds"])
                    prior = SyncRun.objects.filter(sync_job=job,
                        counts_json__profile_fingerprint=fingerprint, counts_json__has_key="catalog_seed_cursor").exclude(
                            counts_json__catalog_seed_cursor="").exclude(pk=run.pk).order_by("-started_at","-created_at","-pk").first()
                    def marker(seed):
                        return seed["recording_id"] + ":" + seed["external_id"]
                    previous_cursor = prior.counts_json.get("catalog_seed_cursor", "") if prior else ""
                    markers = [marker(seed) for seed in options["catalog_seeds"]]
                    if previous_cursor in markers:
                        offset = markers.index(previous_cursor) + 1
                        options["catalog_seeds"] = options["catalog_seeds"][offset:] + options["catalog_seeds"][:offset]
                        seed_cursor = previous_cursor
                    def seed_finished(seed):
                        nonlocal seed_cursor, seeds_processed
                        seed_cursor = marker(seed)
                        seeds_processed += 1
                        run.counts_json = counts()
                        run.save(update_fields=["counts_json","updated_at"])
                    options["seed_finished"] = seed_finished
                for row in collect(enrichment_platform(job), link.url, budget, **options):
                    budget.check_time()
                    observed_at = timezone.now()
                    row_day = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
                    if row_day != day:
                        # A long invocation may cross midnight: old hashes cannot
                        # suppress the next day's evidence or metric observations.
                        run._latest_hashes = None
                    persist_started = time.monotonic()
                    count += persist(run, row, observed_at)
                    persist_seconds += time.monotonic() - persist_started
                    rows_seen += 1
                    incomplete |= row.incomplete
                    if row.limited:
                        provider_limits[row.endpoint] = {"returned": len(row.payload.get("results", [])),
                                                         "reported_total": row.payload.get("total_count")}
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
                    audio_errors = endpoint_errors.get("spotify-audio-features", {})
                    for code in ('rapid_audio_provider_blocked', 'http_401', 'http_403', 'http_429', 'spotify_audio_features_access_unavailable'):
                        if code in audio_errors:
                            raise CollectionError(code)
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
            return True

        streaming_complete = run_streaming_services(streaming[:job_limit], run_job)
        if not streaming_complete or len(streaming) > job_limit or summary["failed"] or summary["skipped"]:
            summary["social_blocked"] = bool(social)
        else:
            remaining = max(0, job_limit - len(streaming))
            run_social_media(social[:remaining], run_job)
        summary["requests"] = total_budget.used
        return summary
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
