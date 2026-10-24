"""Conservative draft discovery from verified customer platform identities.

Only platform IDs and accepted platform URLs identify existing recordings. A
title or ISRC is never a merge key; uncertain attribution stays in review.
"""
from django.db import transaction
from django.db.models import Q

from artists.customer_workspaces import collection_artists
from artists.models import ArtistProfileLink, ArtistResourceGrant
from catalog.models import PlatformObject, Recording, RecordingArtist, RecordingPlatformMatch
from portal.querysets import active_rows
from portal.recording_metrics import canonical_url, locator
from provenance.models import EntityTarget, FieldObservation, ReviewItem, SourceRecord
from .http import CollectionError
from .providers import profile_locator


ENDPOINTS = {"spotify": {"track-details", "spotify-track"},
    "apple-music": {"song-details"}, "soundcloud": {"track-details"}}


def _profiles(conn):
    if not conn.artist_id or conn.status != "active" or conn.organization_id != conn.artist.organization_id:
        return []
    if not active_rows(collection_artists()).filter(pk=conn.artist_id).exists():
        return []
    result = []
    for profile in active_rows(ArtistProfileLink.objects.filter(artist=conn.artist,
            platform=conn.service, verification_status="verified")):
        try:
            result.append((profile, profile_locator(conn.service.slug, profile.url)))
        except (CollectionError, ValueError):
            continue
    return result


def _owned(conn, source, payload, profiles):
    ids = {value for _, value in profiles}
    if conn.service.slug in {"spotify", "apple-music"}:
        artists = payload.get("artists")
        return isinstance(artists, list) and any(isinstance(artist, dict) and
            str(artist.get("id", "")) in ids for artist in artists)
    owner = payload.get("user")
    if not isinstance(owner, dict):
        return False
    permalink = owner.get("permalink")
    if isinstance(permalink, str) and permalink.casefold() in {value.casefold() for value in ids}:
        return True
    for key in ("permalink_url", "link", "url"):
        url = canonical_url(owner.get(key))
        if url and url in {canonical_url(profile.url) for profile, _ in profiles}:
            return True
    # Numeric SoundCloud owner IDs need a profile receipt fetched through this
    # same verified connection. A numeric ID by itself grants no authority.
    owner_id = str(owner.get("id") or "")
    if not owner_id or not source.sync_run_id:
        return False
    receipts = SourceRecord.objects.filter(organization=conn.organization, service=conn.service,
        sync_run=source.sync_run, object_type="user-details", external_id=owner_id)
    return any(isinstance(receipt.payload_json, dict) and
        str(receipt.payload_json.get("id")) == owner_id for receipt in receipts)


def _identities(conn, obj, payload):
    """Include legacy URL-only aliases without inferring identity from names."""
    identity = (conn.service.slug, "track", obj.external_id)
    urls = {canonical_url(payload.get(key)) for key in ("link", "url", "permalink_url")}
    urls.discard("")
    result = {obj.pk}
    candidates = PlatformObject.objects.filter(service=conn.service,
        object_type__in=("track", "recording")).filter(
            Q(external_id=obj.external_id) | Q(external_id="") | Q(object_type="recording"))
    for candidate in candidates.iterator():
        slug, kind, external_id, url = locator(candidate)
        if (slug, kind, external_id) == identity or (conn.service.slug == "soundcloud" and url and url in urls):
            result.add(candidate.pk)
    return result


def _evidence(recording, source, title):
    # Older imports may hold more than one target for the same typed entity;
    # the schema deliberately does not enforce target uniqueness.
    target = EntityTarget.objects.filter(organization=recording.organization, recording=recording).order_by("created_at", "pk").first()
    if target is None:
        target = EntityTarget.objects.create(organization=recording.organization, recording=recording)
    if not FieldObservation.objects.filter(source_record=source, target=target, field_name="title").exists():
        FieldObservation.objects.create(source_record=source, target=target, field_name="title",
            value_json=title, confidence="1.000")


@transaction.atomic
def ensure_catalog_recording(conn, obj, source, row):
    """Return True only when a new draft recording was created.

    Call even when a same-day snapshot is reused, so old evidence can backfill
    catalog rows. Existing editorial fields, grants and decisions are retained.
    """
    slug = conn.service.slug
    payload = source.payload_json
    if slug not in ENDPOINTS or row.kind != "track" or row.endpoint not in ENDPOINTS[slug]:
        return False
    if not isinstance(payload, dict) or row.incomplete or not row.external_id:
        return False
    if (source.organization_id != conn.organization_id or source.service_id != conn.service_id or
            obj.service_id != conn.service_id or obj.object_type != "track" or
            obj.external_id != row.external_id or source.external_id != row.external_id or
            source.object_type != row.endpoint or str(payload.get("id", "")) != row.external_id):
        return False
    if source.sync_run_id and source.sync_run.sync_job.connection_id != conn.pk:
        return False
    profiles = _profiles(conn)
    if source.sync_run_id:
        profile_id = source.sync_run.sync_job.cursor_json.get("profile_link_id")
        if profile_id:
            profiles = [(profile, value) for profile, value in profiles if str(profile.pk) == str(profile_id)]
    if not profiles or not _owned(conn, source, payload, profiles):
        return False
    title = payload.get("name") or payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        return False
    # The runner already serializes collector invocations. Lock the object too,
    # making direct/replayed calls safe against duplicate recording creation.
    PlatformObject.objects.select_for_update().get(pk=obj.pk)
    identities = _identities(conn, obj, payload)
    if active_rows(PlatformObject.objects.filter(pk__in=identities)).count() != len(identities):
        return False
    matches = list(RecordingPlatformMatch.objects.filter(platform_object_id__in=identities)
        .select_related("recording"))
    if matches:
        # Ambiguous, pending, rejected or removed relationships are explicit
        # operator decisions. Do not create another recording around them.
        active_ids = set(active_rows(RecordingPlatformMatch.objects.filter(pk__in=[m.pk for m in matches]))
            .values_list("pk", flat=True))
        recording_ids = {match.recording_id for match in matches}
        if len(recording_ids) != 1 or any(match.review_status != "accepted" or
                match.relationship_type != "full_recording" or match.pk not in active_ids for match in matches):
            return False
        recording = matches[0].recording
        if recording.organization_id != conn.organization_id or recording.archived_at or recording.status == "archived":
            return False
        if not active_rows(Recording.objects.filter(pk=recording.pk)).exists():
            return False
        if not ArtistResourceGrant.objects.filter(artist=conn.artist, recording=recording).exists():
            return False
        RecordingPlatformMatch.objects.get_or_create(recording=recording, platform_object=obj,
            relationship_type="full_recording", defaults={"review_status": "accepted",
                "match_method": "verified_platform_id", "confidence": "1.000"})
        _evidence(recording, source, title)
        return False
    if ReviewItem.objects.filter(issue_type="platform_object_discovered", status__in=("resolved", "dismissed"),
            source_record__organization=conn.organization, source_record__service=conn.service,
            source_record__external_id=row.external_id, source_record__object_type__in=ENDPOINTS[slug],
            candidates__target__organization=conn.organization, candidates__target__artist=conn.artist).exists():
        return False
    fields = {"organization": conn.organization, "title": title, "status": "draft"}
    duration = payload.get("duration_ms")
    if type(duration) is int and 0 <= duration <= 2147483647:
        fields["duration_ms"] = duration
    explicit = payload.get("explicit", payload.get("is_explicit") if slug == "apple-music" else None)
    if type(explicit) is bool:
        fields["explicit"] = explicit
    recording = Recording(**fields)
    recording.full_clean()
    recording.save()
    RecordingArtist.objects.create(recording=recording, artist=conn.artist, role="primary", billing_order=1)
    ArtistResourceGrant.objects.create(artist=conn.artist, recording=recording, capability="manage")
    RecordingPlatformMatch.objects.create(recording=recording, platform_object=obj,
        review_status="accepted", match_method="verified_platform_id", confidence="1.000")
    _evidence(recording, source, title)
    return True
