"""Catalog enrichment follows accepted customer identities without discovery.

Musixmatch contract inspected at ef66119ac3f3.
Names, credits and ISRCs are deliberately never identity or authorization keys.
"""
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from artists.customer_workspaces import collection_artists
from artists.models import ArtistProfileLink, ArtistResourceGrant
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.querysets import active_rows
from provenance.models import SourceRecord, SyncJob
from .http import CollectionError
from .providers import Collected, failure, profile_locator
from .tiktok_catalog import _track_identity

SEED_PLATFORMS = {"spotify-audio-features": "spotify", "musixmatch": "spotify"}
DETAIL_ENDPOINTS = {"musixmatch": "musixmatch-track-details"}
TRACK_ENDPOINTS = {"musixmatch": {"musixmatch-track-details", "musixmatch-track-lyrics"}}


def enrichment_platform(job):
    if job.connection.service.slug == "spotify" and isinstance(job.cursor_json, dict) and job.cursor_json.get("enrichment") == "spotify-audio-features":
        return "spotify-audio-features"
    return job.connection.service.slug


def _id(value, numeric=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value)
    return value if re.fullmatch(r"[0-9]{1,255}" if numeric else r"[A-Za-z0-9]{1,255}", value) else ""


def eligible_seed_profiles():
    return active_rows(ArtistProfileLink.objects.filter(artist__in=active_rows(collection_artists()),
        platform__slug__in=SEED_PLATFORMS.values(), verification_status="verified")).select_related("artist", "platform")


def _current_job(job, *, allow_paused=False):
    try:
        jobs = SyncJob.objects.filter(pk=job.pk, job_type="daily_public_platform")
        if not allow_paused:
            jobs = jobs.filter(enabled=True)
        current = active_rows(jobs).select_related("connection__artist", "connection__service").first()
        if not current:
            return None
        conn = current.connection
        if conn.status != "active" or enrichment_platform(current) not in SEED_PLATFORMS or not conn.artist_id or conn.organization_id != conn.artist.organization_id:
            return None
        if not active_rows(type(conn).objects.filter(pk=conn.pk)).exists() or not active_rows(collection_artists()).filter(pk=conn.artist_id).exists():
            return None
        return current
    except (ValidationError, ValueError, TypeError, AttributeError):
        return None


def seed_profile(job, *, allow_paused=False):
    current = _current_job(job, allow_paused=allow_paused)
    if current is None:
        return None
    try:
        conn = current.connection
        profile = eligible_seed_profiles().filter(pk=current.cursor_json.get("seed_profile_link_id"),
            artist_id=conn.artist_id, platform__slug=SEED_PLATFORMS[enrichment_platform(current)]).first()
        if profile is None:
            return None
        identity = profile_locator(profile.platform.slug, profile.url)
        if profile.external_id and profile.external_id != identity:
            return None
        return profile
    except (ValidationError, ValueError, TypeError, AttributeError, CollectionError):
        return None


def _attributed(profile, external_id):
    """Require a catalog receipt naming the exact currently verified artist ID.

    An old receipt from a different profile/job is insufficient even if titles or
    ISRCs coincide. Legacy receipts without a run can still prove platform IDs.
    """
    return external_id in _attributed_ids(profile, [external_id])


def _attributed_ids(profile, external_ids):
    identity = profile_locator(profile.platform.slug, profile.url)
    endpoints = {"spotify": {"track-details", "spotify-track"}, "apple-music": {"song-details"}}[profile.platform.slug]
    receipts = SourceRecord.objects.filter(organization_id=profile.artist.organization_id,
        service_id=profile.platform_id, external_id__in=external_ids, object_type__in=endpoints).select_related("sync_run__sync_job__connection")
    result = set()
    for receipt in receipts.iterator():
        external_id = receipt.external_id
        body = receipt.payload_json
        if not isinstance(body, dict) or str(body.get("id", "")) != external_id:
            continue
        if receipt.sync_run_id:
            job = receipt.sync_run.sync_job
            if job.connection.artist_id != profile.artist_id or str(job.cursor_json.get("profile_link_id")) != str(profile.pk):
                continue
        artists = body.get("artists")
        if isinstance(artists, list) and any(isinstance(a, dict) and str(a.get("id", "")) == identity for a in artists):
            result.add(external_id)
    return result


def catalog_seeds(job, *, recording_id=None, external_id=None, allow_paused=False, pending_only=True):
    """List authorized identities, or revalidate one exact identity on replay.

    Filtering recordings alone would hide another recording's conflicting match;
    object aliases and every decision for the requested identity remain global.
    """
    if recording_id is not None:
        try:
            recording_id = UUID(str(recording_id))
        except (ValueError, TypeError, AttributeError):
            return []
    profile = seed_profile(job, allow_paused=allow_paused)
    if profile is None:
        return []
    grants = active_rows(ArtistResourceGrant.objects.filter(artist_id=profile.artist_id))
    recordings = active_rows(Recording.objects.filter(organization_id=profile.artist.organization_id,
        archived_at__isnull=True, pk__in=grants.values("recording_id")).exclude(status="archived"))
    if recording_id is not None:
        recordings = recordings.filter(pk=recording_id)
    if external_id is not None and not _id(external_id, numeric=profile.platform.slug == "apple-music"):
        return []
    # Read every decision, including removed/rejected aliases: an accepted row
    # must not route around another operator decision for the same identity.
    objects_query = PlatformObject.objects.filter(service_id=profile.platform_id,
        object_type__in={"track", "recording"})
    if external_id is not None:
        escaped = re.escape(external_id)
        if profile.platform.slug == "spotify":
            url_pattern = rf"^https://open\.spotify\.com/(?:intl-[^/]+/)?track/{escaped}(?:[/?#]|$)"
        else:
            url_pattern = rf"^https://music\.apple\.com/(?:[^/?#]+/)*song/(?:[^/?#]+/)?{escaped}(?:[/?#]|$)|^https://music\.apple\.com/[^#]*[?&]i={escaped}(?:[&#]|$)"
        objects_query = objects_query.filter(Q(external_id=external_id) | Q(url__regex=url_pattern))
    objects = list(objects_query.select_related("service"))
    requested_external_id = external_id
    groups, conflicts = {}, set()
    for obj in objects:
        slug, kind, external_id, url = _track_identity(obj)
        if slug == profile.platform.slug and kind == "track" and _id(external_id, numeric=slug == "apple-music"):
            # Reject contradictory explicit native IDs and URL identities.
            if obj.external_id and obj.external_id != external_id:
                conflicts.update({obj.external_id, external_id})
                continue
            groups.setdefault(external_id, []).append((obj, url))
    result = []
    active_objects = set(active_rows(PlatformObject.objects.filter(pk__in=[o.pk for o in objects])).values_list("pk", flat=True))
    scoped_recordings = set(recordings.values_list("pk", flat=True))
    all_matches = list(RecordingPlatformMatch.objects.filter(platform_object_id__in=[obj.pk for obj in objects]))
    active_matches = set(active_rows(RecordingPlatformMatch.objects.filter(pk__in=[m.pk for m in all_matches])).values_list("pk", flat=True))
    matches_by_object = {}
    for match in all_matches:
        matches_by_object.setdefault(match.platform_object_id, []).append(match)
    attributed = _attributed_ids(profile, groups) if requested_external_id is None else None
    for external_id, aliases in groups.items():
        if requested_external_id is not None and external_id != requested_external_id:
            continue
        if external_id in conflicts:
            continue
        if any(o.pk not in active_objects or o.availability_status in {"removed", "unavailable"} for o, _ in aliases):
            continue
        matches = [match for obj, _ in aliases for match in matches_by_object.get(obj.pk, [])]
        if not matches:
            continue
        recording_ids = {m.recording_id for m in matches}
        if len(recording_ids) != 1 or any(m.pk not in active_matches or m.review_status != "accepted" or m.relationship_type != "full_recording" for m in matches):
            continue
        recording_id = next(iter(recording_ids))
        if recording_id not in scoped_recordings or not (external_id in attributed if attributed is not None else _attributed(profile, external_id)):
            continue
        seed = {"recording_id": str(recording_id), "platform": profile.platform.slug,
            "external_id": external_id, "url": aliases[0][1]}
        if pending_only:
            if enrichment_platform(job) == "musixmatch":
                from .lyrics import has_lyrics
                if has_lyrics(recording_id):
                    continue
        result.append(seed)
    result = sorted(result, key=lambda seed: (seed["external_id"], seed["recording_id"]))
    if pending_only and enrichment_platform(job) == "spotify-audio-features":
        from .audio_features import latest_audio_features_for_recordings
        saved = latest_audio_features_for_recordings(list(recordings), artist=profile.artist)
        selected = {}
        for seed in result:
            if UUID(seed["recording_id"]) not in saved:
                selected.setdefault(seed["recording_id"], seed)
        return list(selected.values())
    return result


def _provider_identity(platform, body, seed):
    if not isinstance(body, dict):
        return ""
    track = body.get("track")
    if not isinstance(track, dict):
        return ""
    external = track.get("external_ids")
    if not isinstance(external, dict):
        return ""
    ids = external.get("spotify_ids") or []
    if not isinstance(ids, list):
        return ""
    declared = external.get("spotify_id")
    # commontrack may contain multiple versions; the track's explicit Spotify ID
    # must agree when present rather than letting a commontrack alias override it.
    if declared and declared != seed["external_id"]:
        return ""
    if seed["external_id"] not in ids and declared != seed["external_id"]:
        return ""
    return _id(track.get("id"), True)


def collect_catalog_service(platform, fetch, seeds, *, seed_finished=None):
    """Request only missing lyrics; preserve exact Spotify/native identity proof."""
    if platform != "musixmatch":
        raise CollectionError("unsupported_platform")
    for seed in seeds:
        try:
            UUID(str(seed["recording_id"]))
            if seed["platform"] != "spotify" or not _id(seed["external_id"]):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CollectionError("invalid_catalog_seed") from None
        tag = {key: str(seed[key]) for key in ("recording_id", "platform", "external_id")}
        try:
            body = fetch("/tracks/lyrics", track="https://open.spotify.com/track/" + tag["external_id"])
            native_id = _provider_identity(platform, body, tag)
            if not native_id:
                raise CollectionError("catalog_identity_mismatch")
            yield Collected("musixmatch-track-lyrics", "track", native_id,
                {**body, "_catalog_seed": tag, "_catalog_identity": body["track"]})
        except CollectionError as exc:
            yield failure("musixmatch-track-lyrics", tag["external_id"], exc)
        if seed_finished is not None:
            seed_finished(seed)


def _validated_seed(conn, obj, source, row=None, *, allow_paused=False):
    if conn.service.slug not in TRACK_ENDPOINTS or not source.sync_run_id or not isinstance(source.payload_json, dict):
        return None
    job = source.sync_run.sync_job
    if job.connection_id != conn.pk or source.organization_id != conn.organization_id or source.service_id != conn.service_id or obj.service_id != conn.service_id or obj.object_type != "track" or source.external_id != obj.external_id or source.object_type not in TRACK_ENDPOINTS[conn.service.slug]:
        return None
    if row is not None and (row.incomplete or row.kind != "track" or row.external_id != obj.external_id or row.endpoint != source.object_type):
        return None
    body = source.payload_json
    tag = body.get("_catalog_seed")
    if not isinstance(tag, dict):
        return None
    try:
        UUID(str(tag.get("recording_id")))
    except (ValueError, TypeError, AttributeError):
        return None
    if tag.get("platform") != SEED_PLATFORMS[conn.service.slug] or not _id(tag.get("external_id")):
        return None
    seeds = catalog_seeds(job, recording_id=tag["recording_id"], external_id=tag["external_id"], allow_paused=allow_paused, pending_only=False)
    seed = next((s for s in seeds if all(tag.get(k) == s[k] for k in ("recording_id", "platform", "external_id"))), None)
    if seed is None:
        return None
    if source.object_type == DETAIL_ENDPOINTS[conn.service.slug]:
        native = _provider_identity(conn.service.slug, body, seed)
    else:
        track = body.get("track")
        identity = body.get("_catalog_identity")
        if not isinstance(track, dict) or not isinstance(identity, dict) or _id(track.get("id"), True) != obj.external_id:
            return None
        native = _provider_identity("musixmatch", {"track": identity}, seed)
    if not native or native != obj.external_id:
        return None
    if not active_rows(PlatformObject.objects.filter(pk=obj.pk)).exclude(availability_status__in={"removed", "unavailable"}).exists():
        return None
    return Recording.objects.get(pk=seed["recording_id"])


@transaction.atomic
def attach_catalog_match(conn, obj, source, row):
    """Attach only new native identities; never rewrite prior match decisions."""
    recording = _validated_seed(conn, obj, source, row)
    if recording is None:
        return False
    PlatformObject.objects.select_for_update().get(pk=obj.pk)
    aliases = _native_objects(obj)
    if active_rows(aliases).exclude(availability_status__in={"removed", "unavailable"}).count() != aliases.count():
        return False
    if RecordingPlatformMatch.objects.filter(platform_object__in=aliases).exists():
        return False
    RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj,
        relationship_type="full_recording", review_status="accepted", match_method="verified_catalog_seed", confidence="1.000")
    return True


def _native_objects(obj):
    # A legacy recording-shaped alias can carry a rejected/removed decision.
    return PlatformObject.objects.filter(service_id=obj.service_id, external_id=obj.external_id,
        object_type__in={"track", "recording"})


def validated_recording(conn, obj, source, row=None, *, allow_paused=False):
    """Fresh authorization; read-only previews may inspect a paused job's evidence.

    Pausing collection does not revoke catalog access. The explicit read mode
    skips only enabled; customer, profile, grant and match decisions stay current.
    """
    recording = _validated_seed(conn, obj, source, row, allow_paused=allow_paused)
    if recording is None:
        return None
    aliases = _native_objects(obj)
    if active_rows(aliases).exclude(availability_status__in={"removed", "unavailable"}).count() != aliases.count():
        return None
    matches = RecordingPlatformMatch.objects.filter(platform_object__in=aliases)
    if matches.count() != 1:
        return None
    if not active_rows(matches).filter(recording=recording, relationship_type="full_recording", review_status="accepted").exists():
        return None
    return recording
