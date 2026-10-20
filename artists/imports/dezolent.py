"""Transactional, source-preserving import of the verified Dezolent site bundle."""

import copy
import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils.text import slugify

from artists.models import Artist, ArtistContact, ArtistProfileLink, Service
from catalog.models import (
    ArtistAsset, ArtistGenre, Asset, Genre, PlatformObject, Recording,
    RecordingArtist, RecordingAsset, RecordingGenre, RecordingIdentifier,
    RecordingPlatformMatch, Release, ReleaseArtist, ReleaseAsset,
    ReleaseIdentifier, ReleasePlatformMatch, ReleaseTrack,
)
from provenance.models import EntityTarget, ReviewCandidate, ReviewItem, SourceRecord
from publishing.models import FanlinkDestination, FanlinkPage, Page, SEORecord, Website
from rights.models import ArtistParty, MusicalWork, Party, RecordingWork

from .dezolent_bundle import canonical_json, payload_hash


NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://dezolent.com/bounced-artists/import/v1")
COLLECTOR = "dezolent-website"
PARSER_VERSION = "dezolent-bounced-v1"
MISSING = object()


def stable_id(key):
    return uuid.uuid5(NAMESPACE, key)


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def iso_date(value):
    return date.fromisoformat(value[:10]) if value else None


def service_slug(name):
    return slugify(name).replace("-music", "-music")[:100]


PROFILE_HOSTS = {
    "open.spotify.com": "spotify", "music.apple.com": "apple-music",
    "soundcloud.com": "soundcloud", "youtube.com": "youtube",
    "instagram.com": "instagram", "facebook.com": "facebook",
    "x.com": "twitter", "twitter.com": "twitter", "tiktok.com": "tiktok",
    "beatport.com": "beatport", "musicbrainz.org": "musicbrainz",
    "isni.org": "isni", "discogs.com": "discogs", "genius.com": "genius",
    "bandcamp.com": "bandcamp",
}


class HeadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_open = False
        self.title = ""
        self.meta = {}
        self.canonical = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.title_open = True
        elif tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and "content" in attrs:
                self.meta[key.lower()] = attrs["content"]
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag == "title":
            self.title_open = False

    def handle_data(self, data):
        if self.title_open:
            self.title += data


class DezolentImporter:
    def __init__(self, bundle):
        self.bundle = bundle
        self.counts = Counter()
        self.conflicts = []
        self.sources = {}
        self.entities = {}
        self.assets_by_url = {}
        self.artist_by_key = {}
        self.recording_by_key = {}
        self.release_by_key = {}
        self.work_by_key = {}
        self.page_by_key = {}
        self.platform_by_link_key = {}
        self.genre_by_name = {}
        self.service_by_slug = {}

    def run(self, *, apply=False):
        if connection.vendor != "postgresql":
            raise ValidationError("The Dezolent import requires PostgreSQL advisory locks.")
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(74231, hashtext(%s))", ["dezolent.com/import-v1"])
            self._resolve_website()
            self._snapshots()
            self._references()
            self._artists()
            self._assets()
            self._recordings()
            self._releases()
            self._links()
            self._pages()
            self._fanlinks()
            self._primary_rights_identity()
            self._review_collisions()
            self._receipt()
            self.verify_round_trip()
            report = {"mode": "apply" if apply else "dry-run", "creates": dict(sorted((k[7:], v) for k, v in self.counts.items() if k.startswith("create:") and v)), "updates": dict(sorted((k[7:], v) for k, v in self.counts.items() if k.startswith("update:") and v)), "conflicts": self.conflicts, "manifest_sha256": self.bundle.manifest_hash, "source_snapshots": len(self.sources)}
            if apply and self.conflicts:
                raise ValidationError(f"Import has {len(self.conflicts)} editorial conflicts; no changes committed.")
            if not apply:
                transaction.set_rollback(True)
            return report

    def _count(self, action, model):
        self.counts[f"{action}:{model._meta.label}"] += 1

    def _save(self, obj, *, created=False):
        obj.full_clean()
        obj.save()
        self._count("create" if created else "update", type(obj))
        return obj

    def _resolve_website(self):
        artist_qs = Artist.objects.select_for_update().filter(bootstrap_key="dezolent")
        artist = artist_qs.first()
        if artist is None:
            matches = list(Artist.objects.select_for_update().filter(slug__iexact="dezolent"))
            if len(matches) != 1:
                raise ValidationError("A unique existing Dezolent artist is required; run seed_artists first.")
            artist = matches[0]
        self.organization = artist.organization
        self.primary_artist = artist
        website = Website.objects.select_for_update().filter(domain="dezolent.com").first()
        if website is None:
            website = self._save(Website(id=stable_id("website:dezolent.com"), organization=self.organization, artist=artist, domain="dezolent.com", repository=self.bundle.singles["site.json"].get("repository", ""), hosting_provider="Netlify"), created=True)
        elif website.artist_id != artist.id or website.organization_id != self.organization.id:
            raise ValidationError("Existing dezolent.com website belongs to another artist or organization.")
        self.website = website
        self.config = copy.deepcopy(website.theme_config or {})
        self.state = copy.deepcopy(self.config.get("dezolent_import") or {})
        self.entity_ids = self.state.setdefault("entity_ids", {})
        self.source_ids = self.state.setdefault("source_ids", {})
        self.field_hashes = self.state.setdefault("field_hashes", {})
        self.entities["artist:dezolent"] = artist

    def _mapped(self, model, key, *, match=None):
        mapped = self.entity_ids.get(key)
        if mapped:
            obj = model.objects.filter(pk=mapped).first()
            if obj is None:
                raise ValidationError(f"Persisted import mapping is stale: {key}")
            return obj
        candidate = model.objects.filter(pk=stable_id(key)).first()
        if candidate is None and match is not None:
            candidate = match()
        return candidate

    def _remember(self, key, obj):
        self.entity_ids[key] = str(obj.pk)
        self.entities[key] = obj
        return obj

    def _merge_fields(self, obj, key, desired):
        hashes = self.field_hashes.setdefault(key, {})
        changed = False
        for field, value in desired.items():
            current = getattr(obj, field)
            if hasattr(current, "pk"):
                current = current.pk
            if isinstance(current, uuid.UUID):
                current = str(current)
            if isinstance(value, uuid.UUID):
                value = str(value)
            if current == value:
                hashes[field] = json_hash(value)
                continue
            previous_hash = hashes.get(field)
            if previous_hash is not None:
                can_update = json_hash(current) == previous_hash
            else:
                can_update = current in (None, "", [], {})
            if can_update:
                setattr(obj, field, value)
                hashes[field] = json_hash(value)
                changed = True
            else:
                self.conflicts.append(f"{key}.{field}")
        if changed:
            self._save(obj)
        return obj

    def _owned(self, model, key, fields, *, match=None):
        obj = self._mapped(model, key, match=match)
        if obj is None:
            obj = model(id=stable_id(key), **fields)
            self._save(obj, created=True)
            self.field_hashes[key] = {field: json_hash(str(value) if isinstance(value, uuid.UUID) else value) for field, value in fields.items()}
        else:
            self._merge_fields(obj, key, fields)
        return self._remember(key, obj)

    def _merge_namespace(self, obj, field, namespace, value, key):
        current = copy.deepcopy(getattr(obj, field) or {})
        previous = current.get(namespace, MISSING)
        if previous == value:
            self.field_hashes.setdefault(key, {})[field + "." + namespace] = json_hash(value)
            return
        prior_hash = self.field_hashes.setdefault(key, {}).get(field + "." + namespace)
        if previous is not MISSING and (prior_hash is None or json_hash(previous) != prior_hash):
            self.conflicts.append(f"{key}.{field}.{namespace}")
            return
        current[namespace] = value
        setattr(obj, field, current)
        self._save(obj)
        self.field_hashes[key][field + "." + namespace] = json_hash(value)

    def _snapshots(self):
        observed = self.bundle.singles["sanity-snapshot.json"].get("observed_at")
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00")) if observed else datetime.combine(date.fromisoformat(self.bundle.manifest["export_day"]), time.min, timezone.utc)
        records = [("manifest", "manifest.json", self.bundle.manifest)]
        records += [("singleton", name, value) for name, value in self.bundle.singles.items()]
        for name, rows in self.bundle.rows.items():
            for row in rows:
                key = row.get("key") or row.get("path")
                if not key:
                    raise ValidationError(f"Missing stable source key in {name}.")
                records.append((name, key, row))
        for kind, key, value in records:
            digest = payload_hash(value)
            map_key = f"{kind}:{key}"
            source = SourceRecord.objects.filter(organization=self.organization, collector=COLLECTOR, object_type=kind, external_id=key, payload_hash=digest).first()
            if source is None:
                source = SourceRecord(id=stable_id(f"source:{map_key}:{digest}"), organization=self.organization, collector=COLLECTOR, collection_method="report_import", parser_version=PARSER_VERSION, object_type=kind, external_id=key, payload_hash=digest, payload_json=value, observed_at=observed_at)
                self._save(source, created=True)
            self.sources[map_key] = source
            self.source_ids[map_key] = str(source.pk)

    def _source(self, kind, key):
        return self.sources[f"{kind}:{key}"]

    def _service(self, slug, name=None, service_type="platform"):
        if slug in self.service_by_slug:
            return self.service_by_slug[slug]
        service = Service.objects.filter(slug=slug).first()
        if service is None:
            service = self._save(Service(slug=slug, name=(name or slug)[:100], service_type=service_type), created=True)
        self.service_by_slug[slug] = service
        return service

    def _references(self):
        for row in self.bundle.rows["platforms"]:
            self._service(row["key"].split(":", 1)[1], row["name"])
        for row in self.bundle.rows["genres"]:
            slug = row["key"].split(":", 1)[1]
            genre = Genre.objects.filter(slug=slug).first()
            if genre is None:
                genre = self._save(Genre(id=stable_id(row["key"]), slug=slug, name=row["name"]), created=True)
            self.genre_by_name[row["name"]] = genre
            self._remember(row["key"], genre)

    def _profile_service(self, url):
        host = (urlsplit(url).hostname or "").lower()
        host = host.removeprefix("www.")
        slug = PROFILE_HOSTS.get(host, "artist-website")
        return self._service(slug, slug.replace("-", " ").title(), "profile")

    def _artists(self):
        for row in self.bundle.rows["artists"]:
            key = row["key"]
            if key == "artist:dezolent":
                artist = self.primary_artist
            else:
                slug = row["suggested_slug"]
                def match(slug=slug):
                    qs = list(Artist.objects.select_for_update().filter(slug__iexact=slug))
                    if len(qs) > 1:
                        raise ValidationError(f"Ambiguous artist slug: {slug}")
                    return qs[0] if qs else None
                artist = self._mapped(Artist, key, match=match)
                if artist is None:
                    artist = self._save(Artist(id=stable_id(key), organization=self.organization, name=row["name"], slug=slug, management_status="unmanaged"), created=True)
                elif artist.organization_id != self.organization.id:
                    raise ValidationError(f"Existing artist belongs to another organization: {key}")
            self._remember(key, artist)
            self.artist_by_key[key] = artist
            if key == "artist:dezolent":
                desired = {"bio_short": row.get("schema", {}).get("description") or "", "bio_long": row.get("bio_long") or ""}
                self._merge_fields(artist, key, desired)
            self._merge_namespace(artist, "public_metadata", "dezolent_website", {k: v for k, v in row.items() if k not in ("key", "bio_long", "profile_urls")}, key)
            for order, url in enumerate(row["profile_urls"]):
                service = self._profile_service(url)
                link = ArtistProfileLink.objects.filter(artist=artist, platform=service, url=url).first()
                if link is None:
                    self._save(ArtistProfileLink(artist=artist, platform=service, url=url, external_id="", verification_status="unverified"), created=True)

    def _assets(self):
        for row in self.bundle.rows["assets"]:
            key = row["key"]
            asset = self._owned(Asset, key, {"organization_id": self.organization.pk, "source_url": row["source_url"], "source_repository": row["source_repository"], "source_path": row["source_path"], "mime_type": row["mime_type"], "sha256": row["sha256"], "visibility": "public"}, match=lambda row=row: Asset.objects.filter(source_url=row["source_url"], organization=self.organization).first())
            self.assets_by_url[row["source_url"]] = asset

    def _asset_for_url(self, url, *, metadata=None):
        if not url:
            return None
        if url in self.assets_by_url:
            return self.assets_by_url[url]
        if not url.startswith(("https://", "http://")):
            return None
        key = f"remote_asset:{hashlib.sha256(url.encode()).hexdigest()}"
        asset = self._owned(Asset, key, {"organization_id": self.organization.pk, "source_url": url, "mime_type": (metadata or {}).get("mimeType") or "", "visibility": "public"}, match=lambda: Asset.objects.filter(source_url=url, organization=self.organization).first())
        self.assets_by_url[url] = asset
        return asset

    def _link_asset(self, model, key, relation, asset, purpose, **extra):
        if asset is None:
            return None
        obj = self._mapped(model, key)
        if obj is None:
            obj = self._save(model(id=stable_id(key), **{relation: self.entities[extra.pop("owner_key")]}, asset=asset, purpose=purpose, **extra), created=True)
        self._remember(key, obj)
        return obj

    def _recordings(self):
        for row in self.bundle.rows["songs"]:
            key = row["key"]
            source = row["source"]
            title = source["title"]
            recording = self._owned(Recording, f"recording:{key}", {"organization_id": self.organization.pk, "title": title, "recording_type": "remix" if "remix" in title.casefold() else "original", "duration_ms": row["derived"]["duration_ms"], "original_release_date": iso_date(source.get("releaseDate")), "date_precision": "day" if source.get("releaseDate") else "unknown", "status": "draft"})
            self.recording_by_key[key] = recording
            for order, credit in enumerate(row["derived"]["credits"], start=1):
                artist = self.artist_by_key[credit["artist_key"]]
                credit_key = f"recording_credit:{key}:{credit['artist_key']}:{credit['role']}"
                self._owned(RecordingArtist, credit_key, {"recording_id": recording.pk, "artist_id": artist.pk, "role": credit["role"], "billing_order": order})
            source_record = self._source("songs", key)
            for field, scheme in (("isrc", "isrc"), ("mbid", "mbid")):
                value = source.get(field) or ""
                if value:
                    id_key = f"recording_identifier:{key}:{scheme}:{value}"
                    self._owned(RecordingIdentifier, id_key, {"recording_id": recording.pk, "scheme": scheme, "value": value, "verification_status": "unverified", "source_record_id": source_record.pk})
            iswc = source.get("iswc") or ""
            if iswc:
                # Source-specific works deliberately remain separate when identifiers collide.
                work = self._owned(MusicalWork, f"work:{key}", {"organization_id": self.organization.pk, "title": title, "iswc": iswc, "status": "unverified"})
                self.work_by_key[key] = work
                self._owned(RecordingWork, f"recording_work:{key}", {"recording_id": recording.pk, "work_id": work.pk, "relationship_type": "embodies", "verification_status": "unverified"})
            for name in source.get("genres") or []:
                genre = self.genre_by_name.get(name)
                if genre is None:
                    raise ValidationError(f"Unknown genre on {key}: {name}")
                self._owned(RecordingGenre, f"recording_genre:{key}:{genre.slug}", {"recording_id": recording.pk, "genre_id": genre.pk})
            cover = self._asset_for_url(row["derived"].get("cover_url"))
            if cover:
                self._owned(RecordingAsset, f"recording_asset:{key}:cover", {"recording_id": recording.pk, "asset_id": cover.pk, "purpose": "cover", "is_primary": True})
            if row["lyrics"] is not None:
                song_id = source["id"]
                lyric_url = f"https://dezolent.com/lyrics/{song_id}-lyrics.txt"
                lyric_asset = self.assets_by_url.get(lyric_url)
                if lyric_asset is None:
                    raise ValidationError(f"Lyric text lacks a matching local asset: {key}")
                self._owned(RecordingAsset, f"recording_asset:{key}:lyrics", {"recording_id": recording.pk, "asset_id": lyric_asset.pk, "purpose": "lyrics", "is_primary": False})

    def _releases(self):
        for row in self.bundle.rows["albums"]:
            key = row["key"]
            source = row["source"]
            release = self._owned(Release, f"release:{key}", {"organization_id": self.organization.pk, "title": source["title"], "release_type": "ep", "release_date": iso_date(source.get("releaseDate")), "date_precision": "day" if source.get("releaseDate") else "unknown", "status": "draft"})
            self.release_by_key[key] = release
            self._owned(ReleaseArtist, f"release_credit:{key}:dezolent", {"release_id": release.pk, "artist_id": self.primary_artist.pk, "role": "primary", "billing_order": 1})
            for track in row["derived"]["tracks"]:
                track_key = f"release_track:{key}:{track['disc_number']}:{track['track_number']}"
                recording = self.recording_by_key[track["song_key"]]
                self._owned(ReleaseTrack, track_key, {"release_id": release.pk, "recording_id": recording.pk, "disc_number": track["disc_number"], "track_number": track["track_number"], "display_title": ""})
            cover = self._asset_for_url(row["derived"].get("cover_url"))
            if cover:
                self._owned(ReleaseAsset, f"release_asset:{key}:cover", {"release_id": release.pk, "asset_id": cover.pk, "purpose": "cover", "is_primary": True})
        for row in self.bundle.rows["songs"]:
            value = row["source"].get("catalogNumber") or ""
            if not value:
                continue
            key = row["key"]
            album_keys = row["derived"]["album_keys"]
            if album_keys:
                release = self.release_by_key[album_keys[0]]
            else:
                # Only materialize a source-asserted single when its identifier
                # otherwise has no release to attach to.
                release_key = f"single:{key}"
                release = self._owned(Release, f"release:{release_key}", {"organization_id": self.organization.pk, "title": row["source"]["title"], "release_type": "single", "release_date": iso_date(row["source"].get("releaseDate")), "date_precision": "day" if row["source"].get("releaseDate") else "unknown", "status": "draft"})
                self.release_by_key[release_key] = release
                self._owned(ReleaseArtist, f"release_credit:{release_key}:dezolent", {"release_id": release.pk, "artist_id": self.primary_artist.pk, "role": "primary", "billing_order": 1})
                self._owned(ReleaseTrack, f"release_track:{release_key}:1:1", {"release_id": release.pk, "recording_id": self.recording_by_key[key].pk, "disc_number": 1, "track_number": 1, "display_title": ""})
            self._owned(ReleaseIdentifier, f"release_identifier:{release.pk}:catalog_number:{value}", {"release_id": release.pk, "scheme": "catalog_number", "value": value, "source_record_id": self._source("songs", key).pk})

    def _platform_type(self, service, url, owner_key):
        parsed = urlsplit(url)
        path = parsed.path.lower()
        if service.slug in ("youtube", "youtube-music"):
            if "/playlist" in path or ("list=" in parsed.query and "/watch" not in path):
                return "playlist"
            return "video"
        if "/album/" in path or "/sets/" in path:
            return "album"
        if "/playlist/" in path:
            return "playlist"
        return "album" if owner_key.startswith("album:") else "track"

    def _links(self):
        for row in self.bundle.rows["links"]:
            if row["kind"] not in ("streaming", "download"):
                continue
            owner_key = row["owner_key"]
            if owner_key not in self.recording_by_key and owner_key not in self.release_by_key:
                raise ValidationError(f"Streaming/download link has no catalog owner: {row['key']}")
            platform = row.get("platform") or "Website download"
            service = self._service(service_slug(platform), platform, "music" if row["kind"] == "streaming" else "website")
            obj_type = self._platform_type(service, row["url"], owner_key) if row["kind"] == "streaming" else "download"
            url = row["url"]
            lookup = {"service": service, "object_type": obj_type, "external_id": "", "url": url}
            matches = list(PlatformObject.objects.filter(**lookup)[:2])
            if len(matches) > 1:
                raise ValidationError(f"Ambiguous platform URL: {row['key']}")
            if matches:
                obj = matches[0]
            else:
                obj = self._save(PlatformObject(id=stable_id(f"platform_object:{service.slug}:{obj_type}:{url}"), **lookup, availability_status="unknown"), created=True)
            self.platform_by_link_key[row["key"]] = obj
            self._remember(f"platform_link:{row['key']}", obj)
            if row["kind"] != "streaming":
                continue
            if owner_key in self.recording_by_key:
                recording = self.recording_by_key[owner_key]
                self._owned(RecordingPlatformMatch, f"recording_match:{owner_key}:{obj.pk}", {"recording_id": recording.pk, "platform_object_id": obj.pk, "relationship_type": "contains" if obj_type == "playlist" else "full_recording", "match_method": "website_assertion", "review_status": "pending"})
            else:
                release = self.release_by_key[owner_key]
                self._owned(ReleasePlatformMatch, f"release_match:{owner_key}:{obj.pk}", {"release_id": release.pk, "platform_object_id": obj.pk, "review_status": "pending"})

    def _pages(self):
        blog_by_key = {row["key"]: row for row in self.bundle.rows["blog-posts"]}
        artist_rows = {row["key"]: row for row in self.bundle.rows["artists"]}
        song_rows = {row["key"]: row for row in self.bundle.rows["songs"]}
        album_rows = {row["key"]: row for row in self.bundle.rows["albums"]}
        for row in self.bundle.rows["pages"]:
            key = row["key"]
            owner_key = row.get("owner_key")
            owner = song_rows.get(owner_key) or album_rows.get(owner_key) or artist_rows.get(owner_key) or blog_by_key.get(owner_key)
            if owner is None:
                raise ValidationError(f"Page has unknown owner: {key}")
            resolved = row.get("resolved") or {}
            source_meta = row.get("source_meta") or {}
            head = HeadParser()
            head.feed(row.get("observed_head_html") or "")
            blog = blog_by_key.get(owner_key)
            blog_source = blog["source"] if blog else None
            title = resolved.get("title") or source_meta.get("title") or head.title or (blog_source or {}).get("title") or row["path"]
            page_payload = {"row": row, "owner": owner}
            page = self._mapped(Page, f"page:{key}", match=lambda row=row: Page.objects.filter(website=self.website, path=row["path"]).first())
            if page is None:
                page = self._save(Page(id=stable_id(f"page:{key}"), website=self.website, path=row["path"], page_type=row["page_type"], title=str(title)[:300], content_json={"dezolent_import": page_payload}, publication_status="draft"), created=True)
                self.field_hashes[f"page:{key}"] = {"page_type": json_hash(row["page_type"]), "title": json_hash(str(title)[:300])}
            else:
                self._merge_fields(page, f"page:{key}", {"page_type": row["page_type"], "title": str(title)[:300]})
            self._remember(f"page:{key}", page)
            self.page_by_key[key] = page
            # The full export row and its owner are retained without transforming
            # empty strings, ordered arrays, lyrics or rich-text body nodes.
            self._merge_namespace(page, "content_json", "dezolent_import", page_payload, f"page:{key}")
            image_url = resolved.get("image_url") or head.meta.get("og:image") or ""
            image_metadata = None
            if blog_source:
                remote = blog_source.get("resolvedOgAsset") or blog_source.get("resolvedFeaturedAsset")
                if remote and remote.get("url") == image_url:
                    image_metadata = remote
            og_asset = self._asset_for_url(image_url, metadata=image_metadata)
            seo_fields = {
                "page_id": page.pk,
                "title": str(resolved.get("title") or head.title or (blog_source or {}).get("seoTitle") or "")[:300],
                "description": str(resolved.get("description") or head.meta.get("description") or (blog_source or {}).get("metaDescription") or ""),
                "canonical_url": resolved.get("canonical_url") or head.canonical or "",
                "robots": str(resolved.get("robots") or head.meta.get("robots") or "")[:100],
                "og_asset_id": og_asset.pk if og_asset else None,
            }
            seo = self._owned(SEORecord, f"seo:{key}", seo_fields, match=lambda page=page: SEORecord.objects.filter(page=page).first())
            self._merge_namespace(seo, "structured_data_overrides", "dezolent_import", {"documents": self.bundle.schema_documents(row)}, f"seo:{key}")

    def _fanlinks(self):
        links_by_owner = defaultdict(list)
        for row in self.bundle.rows["links"]:
            if row["kind"] in ("streaming", "download"):
                links_by_owner[row["owner_key"]].append(row)
        for page_row in self.bundle.rows["pages"]:
            if "fanlink" not in page_row["page_type"]:
                continue
            owner_key = page_row["owner_key"]
            recording = self.recording_by_key.get(owner_key)
            release = self.release_by_key.get(owner_key)
            if (recording is None) == (release is None):
                raise ValidationError(f"Fanlink target is ambiguous: {page_row['key']}")
            slug = ("track-" if recording else "album-") + owner_key.split(":", 1)[1]
            title = recording.title if recording else release.title
            fanlink = self._mapped(FanlinkPage, f"fanlink:{page_row['key']}", match=lambda slug=slug: FanlinkPage.objects.filter(website=self.website, slug=slug).first())
            if fanlink is None:
                fanlink = self._save(FanlinkPage(id=stable_id(f"fanlink:{page_row['key']}"), website=self.website, recording=recording, release=release, slug=slug, title=title, status="draft"), created=True)
                self.field_hashes[f"fanlink:{page_row['key']}"] = {"title": json_hash(title)}
            else:
                self._merge_fields(fanlink, f"fanlink:{page_row['key']}", {"title": title})
                if fanlink.recording_id != (recording.pk if recording else None) or fanlink.release_id != (release.pk if release else None):
                    self.conflicts.append(f"fanlink:{page_row['key']}.target")
            self._remember(f"fanlink:{page_row['key']}", fanlink)
            seen = set()
            for position, link in enumerate(sorted(links_by_owner[owner_key], key=lambda value: value.get("order", 10000))):
                platform = self.platform_by_link_key[link["key"]]
                if platform.pk in seen:
                    continue
                seen.add(platform.pk)
                dest_key = f"fanlink_destination:{page_row['key']}:{link['key']}"
                self._owned(FanlinkDestination, dest_key, {"fanlink_page_id": fanlink.pk, "platform_object_id": platform.pk, "label": link.get("platform") or ("Free download" if link["kind"] == "download" else ""), "sort_order": position, "enabled": True})

    def _primary_rights_identity(self):
        primary = next(row for row in self.bundle.rows["artists"] if row["key"] == "artist:dezolent")
        organization = primary.get("organization") or {}
        person = primary.get("person") or {}
        if organization.get("name") and organization.get("legalName"):
            party = self._owned(Party, "party:dezolent-site-organization", {"organization_id": self.organization.pk, "party_type": "organization", "display_name": organization["name"], "legal_name": organization["legalName"]})
            self._owned(ArtistParty, "artist_party:dezolent:organization", {"artist_id": self.primary_artist.pk, "party_id": party.pk, "relationship_type": "operated_by"})
        if person.get("name") and organization.get("founder"):
            party = self._owned(Party, "party:dezolent-site-person", {"organization_id": self.organization.pk, "party_type": "person", "display_name": person["name"], "legal_name": ""})
            self._owned(ArtistParty, "artist_party:dezolent:performer", {"artist_id": self.primary_artist.pk, "party_id": party.pk, "relationship_type": "performer"})
        email = organization.get("email")
        if email:
            contact = ArtistContact.objects.filter(artist=self.primary_artist, purpose="general", value=email).first()
            if contact is None:
                self._save(ArtistContact(artist=self.primary_artist, purpose="general", value=email, email=email, visibility="public"), created=True)
        for name in primary.get("schema", {}).get("genre") or []:
            genre = self.genre_by_name.get(name)
            if genre:
                self._owned(ArtistGenre, f"artist_genre:dezolent:{genre.slug}", {"artist_id": self.primary_artist.pk, "genre_id": genre.pk})
        image = primary.get("schema", {}).get("image") or {}
        asset = self._asset_for_url(image.get("url") or image.get("contentUrl"))
        if asset:
            self._owned(ArtistAsset, "artist_asset:dezolent:profile", {"artist_id": self.primary_artist.pk, "asset_id": asset.pk, "purpose": "profile", "sort_order": 0})

    def _review_collisions(self):
        for warning in self.bundle.singles["validation.json"]["warnings"]:
            if warning.get("code") != "SHARED_IDENTIFIER":
                continue
            songs = warning["songs"]
            if len(songs) < 2:
                continue
            first_key = f"song:{songs[0]}"
            source = self._source("songs", first_key)
            key = f"review:shared:{warning['field']}:{warning['value']}"
            review = self._owned(ReviewItem, key, {"source_record_id": source.pk, "issue_type": f"shared_{warning['field']}", "status": "open"})
            for song_id in songs:
                song_key = f"song:{song_id}"
                target_value = self.recording_by_key[song_key] if warning["field"] == "isrc" else self.work_by_key[song_key]
                target_kind = "recording" if warning["field"] == "isrc" else "work"
                target = self._owned(EntityTarget, f"target:{target_kind}:{song_key}", {"organization_id": self.organization.pk, f"{target_kind}_id": target_value.pk})
                self._owned(ReviewCandidate, f"candidate:{key}:{song_key}", {"review_item_id": review.pk, "target_id": target.pk})

    def _receipt(self):
        self.state["receipt"] = {"manifest_sha256": self.bundle.manifest_hash, "export_day": self.bundle.manifest["export_day"], "source_commit": self.bundle.manifest["source_commit"], "counts": self.bundle.manifest["counts"], "source_snapshot_count": len(self.sources), "parser_version": PARSER_VERSION}
        desired = copy.deepcopy(self.config)
        desired["dezolent_import"] = self.state
        desired["dezolent_site"] = {"site": self.bundle.singles["site.json"], "platforms": self.bundle.rows["platforms"], "genres": self.bundle.rows["genres"]}
        if self.website.theme_config != desired:
            self.website.theme_config = desired
            self._save(self.website)

    def verify_round_trip(self):
        for map_key, source in self.sources.items():
            actual = SourceRecord.objects.get(pk=source.pk)
            if actual.payload_hash != payload_hash(actual.payload_json) or self.source_ids[map_key] != str(actual.pk):
                raise ValidationError(f"Source snapshot round-trip mismatch: {map_key}")
        if len(self.artist_by_key) != self.bundle.manifest["counts"]["artists"]:
            raise ValidationError("Artist identity coverage mismatch.")
        if len(self.recording_by_key) != self.bundle.manifest["counts"]["songs"]:
            raise ValidationError("Recording coverage mismatch.")
        if len([k for k in self.release_by_key if k.startswith("album:")]) != self.bundle.manifest["counts"]["albums"]:
            raise ValidationError("Declared EP coverage mismatch.")
        if len(self.page_by_key) != self.bundle.manifest["counts"]["pages"]:
            raise ValidationError("Page coverage mismatch.")
        if len(self.assets_by_url) < self.bundle.manifest["counts"]["assets"]:
            raise ValidationError("Local asset coverage mismatch.")
        if len(self.platform_by_link_key) != self.bundle.manifest["counts"]["streaming_links"] + Counter(row["kind"] for row in self.bundle.rows["links"])["download"]:
            raise ValidationError("Streaming/download link coverage mismatch.")
        for row in self.bundle.rows["albums"]:
            release = self.release_by_key[row["key"]]
            positions = list(ReleaseTrack.objects.filter(release=release).order_by("disc_number", "track_number").values_list("recording_id", flat=True))
            expected = [self.recording_by_key[track["song_key"]].pk for track in row["derived"]["tracks"]]
            if positions != expected:
                raise ValidationError(f"EP track order mismatch: {row['key']}")
        for row in self.bundle.rows["pages"]:
            page = self.page_by_key[row["key"]]
            if page.content_json.get("dezolent_import", {}).get("row") != row:
                raise ValidationError(f"Page payload mismatch: {row['key']}")
            seo = SEORecord.objects.get(page=page)
            if seo.structured_data_overrides.get("dezolent_import", {}).get("documents") != self.bundle.schema_documents(row):
                raise ValidationError(f"Schema document mismatch: {row['key']}")
        for row in self.bundle.rows["songs"]:
            page = self.page_by_key.get(f"page:/tracks/{row['source']['id']}")
            if page and page.content_json["dezolent_import"]["owner"]["source"] != row["source"]:
                raise ValidationError(f"Song source field mismatch: {row['key']}")
