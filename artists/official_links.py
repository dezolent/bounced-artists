"""Apply the owner's official-website assertions without changing artist identities.

The checked snapshot is public URL/hash evidence, not live scraping during deploy.
Each row receives a one-time audit receipt so later operator decisions survive.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import RecordingPlatformMatch, ReleasePlatformMatch
from provenance.models import AuditEvent, EntityTarget, SourceRecord
from portal.querysets import active_rows
from portal.validation import safe_external_url
from .customer_workspaces import WORKSPACE_WEBSITES, collection_artists
from .models import ArtistProfileLink, Service

MANIFEST_PATH = Path(__file__).parent / "data" / "official_workspace_links.json"
COLLECTOR = "official-artist-websites"
ACTION = "official_website_link_verified"
HOST_SERVICES = {
    "open.spotify.com": "spotify", "music.apple.com": "apple-music", "soundcloud.com": "soundcloud",
    "youtube.com": "youtube", "youtu.be": "youtube", "music.youtube.com": "youtube",
    "instagram.com": "instagram", "facebook.com": "facebook", "x.com": "twitter", "twitter.com": "twitter",
    "tiktok.com": "tiktok", "beatport.com": "beatport", "musicbrainz.org": "musicbrainz", "isni.org": "isni",
    "discogs.com": "discogs", "genius.com": "genius", "bandcamp.com": "bandcamp", "bandsintown.com": "bandsintown",
    "tidal.com": "tidal", "listen.tidal.com": "tidal", "deezer.com": "deezer", "music.amazon.com": "amazon-music",
    "audiomack.com": "audiomack", "pandora.com": "pandora", "distrokid.com": "distrokid",
}


def canonical_link(url):
    """Compare platform identities, preserving track-vs-album and Apple song IDs."""
    if not isinstance(url, str) or not url:
        raise ValidationError("A public HTTPS link is required.")
    safe_external_url(url)
    parsed = urlsplit(url)
    host = parsed.hostname.lower().removeprefix("www.")
    parts = parsed.path.strip("/").split("/")
    service = HOST_SERVICES.get(host)
    if host.endswith(".bandcamp.com"):
        service = "bandcamp"
    if not service:
        return ("artist-website", "url", urlunsplit(("https", host, parsed.path.rstrip("/"), "", "")))
    if service == "spotify" and len(parts) >= 2 and parts[-2] in {"artist", "track", "album", "playlist"}:
        return (service, parts[-2], parts[-1])
    if service == "apple-music":
        song = parse_qs(parsed.query).get("i", [None])[0]
        if song:
            return (service, "track", song)
        if len(parts) >= 3 and parts[1] in {"artist", "album", "song"}:
            return (service, "track" if parts[1] == "song" else parts[1], parts[-1])
    if service == "youtube":
        video = parse_qs(parsed.query).get("v", [None])[0]
        if video or host == "youtu.be" or parts[0] in {"shorts", "live"}:
            return (service, "video", video or parts[-1])
        playlist = parse_qs(parsed.query).get("list", [None])[0]
        if playlist:
            return (service, "playlist", playlist)
        if parts[0] == "channel" and len(parts) == 2:
            return (service, "channel", parts[-1])
        return (service, "profile", "/".join(parts).casefold())
    if service == "soundcloud":
        return (service, "profile" if len(parts) == 1 else "track", "/".join(parts).casefold())
    if service == "instagram" and parts[0] in {"p", "reel"} and len(parts) == 2:
        return (service, "post", parts[-1])
    if service in {"twitter", "instagram", "tiktok", "facebook"}:
        return (service, "profile", "/".join(parts).casefold())
    return (service, "url", urlunsplit(("https", host, parsed.path.rstrip("/"), parsed.query, "")))


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text())


def validate_manifest(manifest):
    if manifest.get("version") != 1 or not isinstance(manifest.get("artists"), list):
        raise ValidationError("Invalid official website evidence.")
    seen = set()
    for entry in manifest["artists"]:
        key, website = entry.get("key"), entry.get("website")
        if key not in WORKSPACE_WEBSITES or key in seen or website != WORKSPACE_WEBSITES[key]:
            raise ValidationError("Official website must match the authorized workspace identity.")
        seen.add(key)
        for url in entry.get("profiles", []):
            canonical_link(url)
        for item in entry.get("links", []):
            canonical_link(item["url"])
            if not item.get("pages") or any(urlsplit(page).hostname != urlsplit(website).hostname for page in item["pages"]):
                raise ValidationError("Link evidence must come from the artist's own official website.")


def service_key(slug):
    return "youtube" if slug == "youtube-music" else slug


def existing_identity(url):
    try:
        return canonical_link(url)
    except (ValidationError, ValueError):
        return None


@transaction.atomic
def verify_links(manifest, *, apply=False):
    validate_manifest(manifest)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    result = {"apply": apply, "manifest_sha256": digest, "changes": 0, "artists": [], "missing_workspaces": []}
    now = timezone.now()
    if apply:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1837012027])
    for entry in manifest["artists"]:
        artist = collection_artists().filter(bootstrap_key=entry["key"]).first()
        if artist is None:
            result["missing_workspaces"].append(entry["key"])
            continue
        receipt_rows = AuditEvent.objects.filter(organization=artist.organization, target__artist=artist,
            action=ACTION, change_json__manifest_sha256=digest).values_list("change_json", flat=True)
        receipted = {(row["model"], row["object_id"]) for row in receipt_rows}
        profiles = ArtistProfileLink.objects.filter(artist=artist).select_related("platform")
        existing = list(profiles.select_for_update(of=("self",)) if apply else profiles)
        identities = {row.pk: existing_identity(row.url) for row in existing}
        proof_exists = SourceRecord.objects.filter(organization=artist.organization, collector=COLLECTOR,
            object_type="official_website_links", external_id=entry["website"], payload_hash=digest).exists()
        active_ids = set(active_rows(ArtistProfileLink.objects.filter(artist=artist)).values_list("pk", flat=True))
        counters = {"key": entry["key"], "profiles_create": 0, "profiles_verify": 0, "recording_links_accept": 0, "release_links_accept": 0, "audit_receipts": 0,
                    "invalid_existing_links": sum(value is None for value in identities.values())}
        actions, creates = [], []
        for url in entry["profiles"]:
            identity = canonical_link(url)
            candidates = [row for row in existing if service_key(row.platform.slug) == identity[0] and identities[row.pk] == identity]
            if candidates:
                for row in candidates:
                    if row.pk in active_ids and (row._meta.label_lower, str(row.pk)) not in receipted and row not in [a[0] for a in actions]:
                        actions.append((row, "verification_status", "verified", "profiles_verify"))
            elif not proof_exists and identity not in {canonical_link(value) for value in creates}:
                creates.append(url)
        trusted = {canonical_link(item["url"]) for item in entry["links"]}
        for model, parent, counter in ((RecordingPlatformMatch, "recording", "recording_links_accept"), (ReleasePlatformMatch, "release", "release_links_accept")):
            rows = active_rows(model.objects.filter(**{
                f"{parent}__organization": artist.organization, f"{parent}__archived_at__isnull": True,
                f"{parent}__artist_grants__artist": artist,
            })).select_related("platform_object__service")
            if apply:
                rows = rows.select_for_update(of=("self",))
            for row in rows:
                if not row.platform_object.url or (row._meta.label_lower, str(row.pk)) in receipted:
                    continue
                identity = existing_identity(row.platform_object.url)
                if identity is None:
                    counters["invalid_existing_links"] += 1
                    continue
                if identity in trusted and identity[0] == service_key(row.platform_object.service.slug):
                    actions.append((row, "review_status", "accepted", counter))
        counters["profiles_create"] = len(creates)
        for row, field, value, counter in actions:
            counters[counter] += getattr(row, field) != value
        counters["audit_receipts"] = len(actions) + len(creates)
        result["changes"] += sum(counters[k] for k in ("profiles_create", "profiles_verify", "recording_links_accept", "release_links_accept"))
        result["artists"].append(counters)
        if not apply or not (actions or creates):
            continue
        website_service, _ = Service.objects.get_or_create(slug="artist-website", defaults={"name": "Artist website", "service_type": "website"})
        source, _ = SourceRecord.objects.get_or_create(organization=artist.organization, service=website_service,
            collector=COLLECTOR, collection_method="manual", parser_version="official-links-v1",
            object_type="official_website_links", external_id=entry["website"], payload_hash=digest,
            defaults={"payload_json": {"verified_on": manifest["verified_on"], "website_evidence": entry}, "observed_at": now})
        target, _ = EntityTarget.objects.get_or_create(organization=artist.organization, artist=artist)
        for url in creates:
            slug = canonical_link(url)[0]
            service, _ = Service.objects.get_or_create(slug=slug, defaults={"name": slug.replace("-", " ").title(), "service_type": "profile"})
            row = ArtistProfileLink(artist=artist, platform=service, url=url)
            actions.append((row, "verification_status", "verified", "profiles_create"))
        for row, field, value, counter in actions:
            previous = getattr(row, field)
            setattr(row, field, value)
            if isinstance(row, ArtistProfileLink):
                row.verified_at = now
            row.full_clean()
            if row._state.adding:
                row.save()
            else:
                row.save(update_fields=[field, "updated_at"] + (["verified_at"] if isinstance(row, ArtistProfileLink) else []))
            AuditEvent.objects.create(organization=artist.organization, target=target, action=ACTION,
                target_label=f"{entry['key']} / {row._meta.label_lower}", change_json={
                    "manifest_sha256": digest, "website": entry["website"], "source_record_id": str(source.pk),
                    "model": row._meta.label_lower, "object_id": str(row.pk), "from_status": previous, "to_status": value,
                })
    return result
