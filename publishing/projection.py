"""Staff-reviewed, immutable public projections. No delivery or network operations."""
from dataclasses import dataclass
from datetime import date
import hashlib
import html
import json
import re
from urllib.parse import urlsplit, parse_qsl
from uuid import UUID, uuid5

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils.html import strip_tags

PUBLIC_NAMESPACE = UUID('f2987ffb-0f52-4260-b873-813e1b5370c7')
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
MAX_PUBLIC_LYRICS_BYTES = 256 * 1024
KEYS = {
    'artist': {'id','name','slug','bio_short','bio_long','location','artwork','profile_links'},
    'recording': {'id','title','version_title','recording_type','duration_ms','explicit','original_release_date','date_precision','artists','genres','artwork','platform_links','release_ids','fanlink_ids'},
    'release': {'id','title','release_type','release_date','date_precision','artists','artwork','tracks','platform_links','fanlink_ids'},
    'credit': {'id','name','role','billing_order'},
    'genre': {'name','slug'},
    'track': {'recording_id','disc_number','track_number','display_title'},
    'artwork': {'url','alt','width','height','mime_type'},
    'destination': {'service','label','url','relationship_type'},
    'profile': {'service','label','url'},
    'fanlink': {'id','slug','title','target_type','target_id','artwork','destinations'},
}
V1_KEYS = {kind: set(fields) for kind, fields in KEYS.items()}
KEYS['recording'] |= {'identifiers', 'labels', 'works', 'lyrics', 'language', 'disambiguation'}
KEYS['release'] |= {'identifiers', 'labels', 'genres', 'lyrics', 'edition', 'country', 'language', 'script', 'packaging', 'barcode', 'catalog_number', 'disambiguation', 'duration_ms', 'duration_incomplete', 'release_group', 'media'}
KEYS['credit'] |= {'credited_as', 'join_phrase'}
KEYS['track'] |= {'duration_ms', 'credit_text', 'mbid', 'medium_id'}
KEYS.update({
    'identifier': {'scheme', 'value'},
    'release_group': {'id', 'title', 'primary_type', 'secondary_types', 'mbid', 'genres'},
    'medium': {'id', 'position', 'title', 'format'},
    'label': {'id', 'name', 'mbid', 'is_primary'},
    'release_label': {'id', 'name', 'mbid', 'is_primary', 'catalog_number'},
    'work': {'id', 'title', 'relationship_type', 'identifiers'},
    'lyrics': {'id', 'text', 'mime_type'},
})
IDENTIFIER_SCHEMES = {
    'recording': {'ISRC', 'MBID'},
    'release': {'UPC', 'EAN', 'GTIN', 'BARCODE', 'MBID', 'CATALOG_NUMBER'},
    'work': {'ISWC', 'MBID'},
}


def valid_identifier(kind, scheme, value):
    if scheme not in IDENTIFIER_SCHEMES[kind] or not isinstance(value, str) or not value or text(value) != value:
        return False
    if scheme == 'MBID':
        try:
            return str(UUID(value)) == value.lower()
        except (ValueError, TypeError, AttributeError):
            return False
    if scheme == 'ISRC': return re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}[0-9]{7}', value) is not None
    if scheme == 'ISWC': return re.fullmatch(r'T-?[0-9]{3}\.?[0-9]{3}\.?[0-9]{3}-?[0-9]', value) is not None
    if scheme in {'UPC', 'EAN', 'GTIN', 'BARCODE'}: return re.fullmatch(r'[0-9]{8,14}', value) is not None
    return len(value) <= 100


# This is deliberately a finite reviewed policy. Unknown services fail closed.
URL_POLICY = {
    'spotify': ({'open.spotify.com'}, r'/(?:intl-[a-z]{2}/)?(?:track|album|artist|playlist)/[A-Za-z0-9]+/?', set()),
    'apple-music': ({'music.apple.com'}, r'/[a-z]{2}/(?:album|artist|song|music-video|playlist)/[^?#]+', {'i','l'}),
    'apple_music': ({'music.apple.com'}, r'/[a-z]{2}/(?:album|artist|song|music-video|playlist)/[^?#]+', {'i','l'}),
    'youtube': ({'www.youtube.com','youtube.com','youtu.be','music.youtube.com'}, r'/(?:watch|playlist|channel/[^/]+|@[^/]+|[A-Za-z0-9_-]+)/*', {'v','list'}),
    'youtube-music': ({'music.youtube.com'}, r'/(?:watch|playlist|channel/[^/]+)/*', {'v','list'}),
    'soundcloud': ({'soundcloud.com','www.soundcloud.com'}, r'/[^?#]+', set()),
    'bandcamp': (set(), r'/[^?#]*', set()),
    'deezer': ({'www.deezer.com','deezer.com'}, r'/(?:[a-z]{2}/)?(?:track|album|artist)/[0-9]+/?', set()),
    'tidal': ({'tidal.com','listen.tidal.com'}, r'/(?:browse/)?(?:track|album|artist)/[0-9]+/?', set()),
    'amazon-music': ({'music.amazon.com'}, r'/(?:albums|tracks|artists)/[A-Za-z0-9]+/?', {'trackAsin'}),
    'instagram': ({'www.instagram.com','instagram.com'}, r'/[A-Za-z0-9_.]+/?', set()),
    'facebook': ({'www.facebook.com','facebook.com'}, r'/[A-Za-z0-9_.]+/?', set()),
    'tiktok': ({'www.tiktok.com','tiktok.com'}, r'/@[A-Za-z0-9_.]+(?:/video/[0-9]+)?/?', set()),
}


def public_id(kind, canonical_id):
    return str(uuid5(PUBLIC_NAMESPACE, f'{kind}:{canonical_id}'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def text(value):
    return ''.join(c for c in strip_tags(html.unescape(str(value or ''))) if c in '\n\t' or ord(c) >= 32)


def validate_public_url(service, url):
    try:
        parts = urlsplit(url)
        policy = URL_POLICY.get(service)
        if not policy or parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443) or parts.fragment:
            raise ValueError
        host = parts.hostname or ''
        hosts, path_pattern, query_keys = policy
        if service == 'bandcamp':
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.bandcamp\.com', host): raise ValueError
        elif host not in hosts:
            raise ValueError
        if not re.fullmatch(path_pattern, parts.path): raise ValueError
        if any(key not in query_keys or len(value) > 256 for key, value in parse_qsl(parts.query, keep_blank_values=True)): raise ValueError
        if any(c in url for c in ('\\','\r','\n','\t')): raise ValueError
    except (ValueError, TypeError):
        raise ValidationError('Destination is outside the reviewed HTTPS service policy.') from None
    return url


def _keys(value, kind, schema_keys=KEYS):
    if not isinstance(value, dict) or set(value) != schema_keys[kind]:
        raise ValidationError(f'Invalid public {kind} fields.')
    integer_fields = {'billing_order','disc_number','track_number','width','height','duration_ms','position'}
    list_fields = {'profile_links','artists','genres','platform_links','release_ids','fanlink_ids','tracks','destinations','identifiers','labels','works','lyrics','media','secondary_types'}
    nullable = {'original_release_date','release_date','duration_ms','explicit','artwork','mbid','medium_id','release_group'}
    for key, item in value.items():
        if item is None and key in nullable: continue
        if key in ('artwork', 'release_group'):
            if not isinstance(item,dict): raise ValidationError('Invalid artwork.')
        elif key in list_fields:
            if not isinstance(item,list): raise ValidationError('Invalid public array.')
        elif key in integer_fields:
            if type(item) is not int or item < (0 if key in {'billing_order','duration_ms'} else 1): raise ValidationError('Invalid public integer.')
        elif key in {'explicit', 'is_primary', 'duration_incomplete'}:
            if type(item) is not bool: raise ValidationError('Invalid public explicit flag.')
        elif not isinstance(item,str) or text(item) != item:
            raise ValidationError('Public text must be plain text.')
        if key in {'id','recording_id','target_id','medium_id'} or key in {'release_ids','fanlink_ids'}:
            try:
                for identifier in item if isinstance(item,list) else [item]: UUID(identifier)
            except (ValueError,TypeError,AttributeError): raise ValidationError('Invalid public identifier.') from None
    return value


def validate_payload(payload, schema_version=SCHEMA_VERSION):
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError('Unsupported public schema version.')
    schema_keys = V1_KEYS if schema_version == 1 else KEYS
    def fields(value, kind): return _keys(value, kind, schema_keys)
    def identifiers(items, kind):
        for identifier in items:
            fields(identifier, 'identifier')
            if not valid_identifier(kind, identifier['scheme'], identifier['value']):
                raise ValidationError('Invalid public catalog identifier.')
    def expanded(item, kind):
        identifiers(item['identifiers'], kind)
        for label in item['labels']:
            fields(label, 'label' if kind == 'recording' else 'release_label')
            if label['mbid'] is not None and not valid_identifier('recording', 'MBID', label['mbid']):
                raise ValidationError('Invalid label MBID.')
        for lyric in item['lyrics']:
            fields(lyric, 'lyrics')
            if lyric['mime_type'] != 'text/plain' or len(lyric['text'].encode('utf-8')) > MAX_PUBLIC_LYRICS_BYTES:
                raise ValidationError('Invalid public lyric text.')
        if kind == 'recording':
            for work in item['works']:
                fields(work, 'work')
                identifiers(work['identifiers'], 'work')
        else:
            group = item['release_group']
            if group is not None:
                fields(group, 'release_group')
                if len(group['secondary_types']) > 10 or any(not isinstance(value,str) or not value or len(value)>30 or text(value)!=value for value in group['secondary_types']):
                    raise ValidationError('Invalid release group types.')
                if group['mbid'] is not None and not valid_identifier('recording', 'MBID', group['mbid']):
                    raise ValidationError('Invalid release group MBID.')
                for genre in group['genres']: fields(genre, 'genre')
            for medium in item['media']: fields(medium, 'medium')
            for track in item['tracks']: fields(track, 'track')
            medium_ids = {medium['id'] for medium in item['media']}
            if len(medium_ids) != len(item['media']) or any(track['medium_id'] is not None and track['medium_id'] not in medium_ids for track in item['tracks']):
                raise ValidationError('Invalid public medium relation.')
            for genre in item['genres']: fields(genre, 'genre')
            for track in item['tracks']:
                if track['mbid'] is not None and not valid_identifier('recording', 'MBID', track['mbid']):
                    raise ValidationError('Invalid track MBID.')
    if not isinstance(payload, dict) or set(payload) != {'artist','recordings','releases','fanlinks'}:
        raise ValidationError('Invalid public payload fields.')
    fields(payload['artist'], 'artist')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',payload['artist']['slug']): raise ValidationError('Invalid artist slug.')
    def artwork(value):
        if value is not None:
            fields(value, 'artwork')
            expected = rf"/api/public/v1/artists/{re.escape(payload['artist']['slug'])}/artwork/[0-9a-f-]{{36}}/"
            if not re.fullmatch(expected, value['url']): raise ValidationError('Invalid public artwork URL.')
            if value['mime_type'] != 'image/webp': raise ValidationError('Invalid public artwork type.')
    def links(items, kind):
        if not isinstance(items,list): raise ValidationError('Invalid public links.')
        for item in items:
            fields(item, kind)
            validate_public_url(item['service'], item['url'])
    artwork(payload['artist']['artwork'])
    links(payload['artist']['profile_links'], 'profile')
    for kind, plural in [('recording','recordings'), ('release','releases'), ('fanlink','fanlinks')]:
        if not isinstance(payload[plural], list): raise ValidationError('Invalid public collection.')
        seen = set()
        for item in payload[plural]:
            fields(item, kind); artwork(item['artwork'])
            if item['id'] in seen: raise ValidationError('Duplicate public identifier.')
            seen.add(item['id'])
            if kind == 'fanlink':
                links(item['destinations'], 'destination')
                if item['target_type'] not in ('recording','release') or not re.fullmatch(r'[a-zA-Z0-9_-]+',item['slug']): raise ValidationError('Invalid fanlink target or slug.')
                continue
            links(item['platform_links'], 'destination')
            if schema_version == 2: expanded(item, kind)
            for credit in item['artists']: fields(credit, 'credit')
            precision = item['date_precision']
            if precision not in ('unknown','year','month','day'): raise ValidationError('Invalid date precision.')
            date_value = item['original_release_date' if kind == 'recording' else 'release_date']
            pattern = {'year': r'\d{4}', 'month': r'\d{4}-\d{2}', 'day': r'\d{4}-\d{2}-\d{2}'}.get(precision)
            if date_value is not None:
                if not pattern or not re.fullmatch(pattern,date_value): raise ValidationError('Invalid date value.')
                try: date.fromisoformat(date_value + {'year':'-01-01','month':'-01','day':''}[precision])
                except ValueError: raise ValidationError('Invalid date value.') from None
            if kind == 'recording':
                for genre in item['genres']: fields(genre, 'genre')
            else:
                for track in item['tracks']: fields(track, 'track')
    recordings={item['id'] for item in payload['recordings']}
    releases={item['id'] for item in payload['releases']}
    fanlinks={item['id'] for item in payload['fanlinks']}
    for item in payload['recordings']:
        if not set(item['release_ids']) <= releases or not set(item['fanlink_ids']) <= fanlinks: raise ValidationError('Unknown public relation.')
    for item in payload['releases']:
        if not set(item['fanlink_ids']) <= fanlinks or any(track['recording_id'] not in recordings for track in item['tracks']): raise ValidationError('Unknown public track or fanlink.')
    for item in payload['fanlinks']:
        if item['target_id'] not in (recordings if item['target_type']=='recording' else releases): raise ValidationError('Unknown public target.')
    return payload


def artwork_urls(payload):
    objects=[payload['artist'],*payload['recordings'],*payload['releases'],*payload['fanlinks']]
    return {obj['artwork']['url'] for obj in objects if obj.get('artwork')}


def normalize_selection(selection, schema_version=SCHEMA_VERSION):
    keys = ('recordings', 'releases', 'fanlinks', 'artwork') + (('lyrics', 'works') if schema_version == 2 else ())
    if not isinstance(selection,dict) or set(selection)-set(keys):
        raise ValidationError('Invalid publication selection.')
    normalized={}
    for key in keys:
        values=selection.get(key,[])
        if not isinstance(values,(list,tuple)) or len(values)>1000:
            raise ValidationError('Invalid publication selection.')
        try: normalized[key]=sorted({str(UUID(str(value))) for value in values})
        except (ValueError,TypeError,AttributeError): raise ValidationError('Invalid publication selection.') from None
    return normalized


@dataclass(frozen=True)
class CatalogPreview:
    website_id: str
    selection: dict
    payload: dict
    source_checksum: str
    checksum: str
    artwork_versions: tuple


def _staff(actor):
    if not actor or not actor.is_active or not actor.is_staff: raise PermissionDenied('Staff review is required.')


class Builder:
    def __init__(self, website, selection, lock=False):
        from artists.models import ArtistResourceGrant
        from catalog.models import Recording, Release, Asset
        from publishing.models import FanlinkPage
        from rights.models import MusicalWork
        self.lock = lock
        self.sources = {}
        self.website = self.track(type(website).objects.get(pk=website.pk))
        self.artist = self.track(self.website.artist)
        if self.website.organization_id != self.artist.organization_id or getattr(self.artist, 'archived_at', None) or getattr(self.website, 'archived_at', None): raise ValidationError('Website is unavailable.')
        selection=normalize_selection(selection)
        self.selection = {}
        self.selected = {}
        self.artwork_versions = set()
        for key, model in [('recordings',Recording),('releases',Release),('fanlinks',FanlinkPage),('artwork',Asset),('lyrics',Asset),('works',MusicalWork)]:
            ids=selection[key]
            self.selection[key] = ids
            from portal.querysets import active_rows
            rows = list(active_rows(model.objects.filter(pk__in=ids)).order_by('pk'))
            if len(rows) != len(ids): raise ValidationError('Selected resource is unavailable.')
            self.selected[key] = {str(row.pk): self.track(row) for row in rows}
            for row in self.selected[key].values():
                if getattr(row,'archived_at',None): raise ValidationError('Archived content cannot be approved.')
                if key == 'fanlinks':
                    if row.website_id != self.website.pk or row.status != 'approved': raise ValidationError('Fanlink is not approved for this website.')
                else:
                    if row.organization_id != self.website.organization_id: raise ValidationError('Selection must share website organization.')
                    grant = ArtistResourceGrant.objects.filter(artist=self.artist, **{('asset' if key in ('artwork', 'lyrics') else key[:-1]): row}).first()
                    if not grant: raise ValidationError('Selection requires an explicit artist grant.')
                    self.track(grant)
                    if key not in ('artwork', 'lyrics', 'works') and row.status != 'approved': raise ValidationError('Selected content requires approval.')

    def track(self, obj):
        if self.lock:
            obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        self.sources[f'{obj._meta.label}:{obj.pk}'] = {f.attname: f.value_from_object(obj) for f in obj._meta.concrete_fields}
        return obj

    def rows(self, queryset):
        from portal.querysets import active_rows
        if hasattr(queryset.model,'archived_at'):
            queryset = queryset.filter(archived_at__isnull=True)
        return [self.track(obj) for obj in active_rows(queryset).order_by('pk')]

    def artwork(self, relations):
        for relation in self.rows(relations):
            asset = self.selected['artwork'].get(str(relation.asset_id))
            if not asset: continue
            if asset.visibility != 'public' or not asset.rights_approved or not asset.rights_basis or not asset.current_version_id: raise ValidationError('Artwork requires approved rights and an immutable uploaded version.')
            version = self.track(asset.current_version)
            if version.asset_id != asset.pk or version.mime_type not in ('image/jpeg','image/png','image/webp') or not version.preview_file or not version.preview_width or not version.preview_height: raise ValidationError('Invalid public raster version.')
            self.artwork_versions.add(str(version.pk))
            return {'url': f'/api/public/v1/artists/{self.artist.slug}/artwork/{public_id("artwork",version.pk)}/', 'alt': text(asset.alt_text), 'width':version.preview_width,'height':version.preview_height,'mime_type':'image/webp'}
        return None

    def credits(self, obj):
        result = []
        for credit in self.rows(obj.credits.all()):
            artist = self.track(credit.artist)
            if artist.organization_id != self.website.organization_id or getattr(artist,'archived_at',None): raise ValidationError('Credit artist is unavailable.')
            result.append({'id':public_id('artist',artist.pk),'name':text(credit.credited_as or artist.name),'credited_as':text(credit.credited_as),'join_phrase':text(credit.join_phrase),'role':text(credit.role),'billing_order':credit.billing_order})
        return sorted(result,key=lambda item:(item['billing_order'],item['id']))

    def destinations(self, obj, kind):
        from catalog.models import RecordingPlatformMatch, ReleasePlatformMatch
        model = RecordingPlatformMatch if kind == 'recording' else ReleasePlatformMatch
        result = []
        for match in self.rows(model.objects.filter(**{kind:obj},review_status='accepted')):
            platform = self.track(match.platform_object); service = self.track(platform.service)
            try: url = validate_public_url(service.slug, platform.url)
            except ValidationError: continue
            result.append({'service':service.slug,'label':text(service.name),'url':url,'relationship_type':text(getattr(match,'relationship_type','release'))})
        return result

    def identifiers(self, obj, kind):
        result = set()
        for identifier in self.rows(obj.identifiers.all()):
            if getattr(identifier, 'verification_status', '') == 'rejected': continue
            scheme = identifier.scheme.upper()
            if valid_identifier(kind, scheme, identifier.value): result.add((scheme, identifier.value))
        # Retain the existing work ISWC without inferring an identifier for a recording.
        if kind == 'work' and obj.iswc and valid_identifier(kind, 'ISWC', obj.iswc):
            result.add(('ISWC', obj.iswc))
        return [{'scheme': scheme, 'value': value} for scheme, value in sorted(result)]

    def labels(self, obj, kind):
        from artists.models import ArtistResourceGrant
        result = []
        for association in sorted(self.rows(obj.labels.all()), key=lambda row: (row.position, str(row.pk))):
            label = self.track(association.label)
            if label.organization_id != self.website.organization_id or label.archived_at:
                raise ValidationError('Public label must belong to this organization and be active.')
            grant = ArtistResourceGrant.objects.filter(artist=self.artist, label=label).first()
            if not grant: raise ValidationError('Public labels require an explicit artist grant.')
            self.track(grant)
            row = {'id': public_id('label', label.pk), 'name': text(label.name),
                   'mbid': str(label.mbid) if label.mbid else None, 'is_primary': association.is_primary}
            if kind == 'release': row['catalog_number'] = text(association.catalog_number)
            result.append(row)
        return result

    def release_group(self, obj):
        from artists.models import ArtistResourceGrant
        from catalog.models import ReleaseGroupGenre
        if not obj.release_group_id: return None
        group = self.track(obj.release_group)
        if group.organization_id != self.website.organization_id or group.archived_at or group.status != 'approved':
            raise ValidationError('Public release groups must be active and approved in this organization.')
        grant = ArtistResourceGrant.objects.filter(artist=self.artist, release_group=group).first()
        if not grant: raise ValidationError('Public release groups require an explicit artist grant.')
        self.track(grant)
        genres = []
        for association in self.rows(ReleaseGroupGenre.objects.filter(release_group=group)):
            genre = self.track(association.genre)
            genres.append({'name': text(genre.name), 'slug': genre.slug})
        return {'id': public_id('release_group', group.pk), 'title': text(group.title), 'primary_type': text(group.primary_type),
                'secondary_types': [text(value) for value in group.secondary_types], 'mbid': str(group.mbid) if group.mbid else None, 'genres': genres}

    def works(self, obj):
        from rights.models import RecordingWork
        result = []
        for association in self.rows(RecordingWork.objects.filter(recording=obj, verification_status='verified')):
            work = self.selected['works'].get(str(association.work_id))
            if not work: continue
            result.append({'id': public_id('work', work.pk), 'title': text(work.title),
                           'relationship_type': text(association.relationship_type), 'identifiers': self.identifiers(work, 'work')})
        return result

    def lyrics(self, relations):
        result = []
        for relation in self.rows(relations):
            asset = self.selected['lyrics'].get(str(relation.asset_id))
            if not asset: continue
            if asset.category != 'lyrics_text' or asset.visibility != 'public' or not asset.rights_approved or not asset.rights_basis or not asset.current_version_id:
                raise ValidationError('Lyrics require approved rights and an immutable uploaded text version.')
            version = self.track(asset.current_version)
            if version.asset_id != asset.pk or version.mime_type != 'text/plain' or not version.file or version.byte_size > MAX_PUBLIC_LYRICS_BYTES:
                raise ValidationError('Public lyrics must be a bounded plain text upload.')
            try:
                with version.file.open('rb') as stream:
                    raw = stream.read(MAX_PUBLIC_LYRICS_BYTES + 1)
                if len(raw) > MAX_PUBLIC_LYRICS_BYTES or len(raw) != version.byte_size or hashlib.sha256(raw).hexdigest() != version.sha256:
                    raise ValueError
                value = raw.decode('utf-8')
                if '\x00' in value: raise ValueError
            except (OSError, ValueError, UnicodeDecodeError):
                raise ValidationError('The approved lyric upload is unavailable or invalid.') from None
            result.append({'id': public_id('lyrics', version.pk), 'text': text(value), 'mime_type': 'text/plain'})
        return result

    def build(self):
        from catalog.models import ArtistAsset, RecordingAsset, RecordingGenre, ReleaseAsset, ReleaseGenre
        a = self.artist
        profiles = []
        for link in self.rows(a.profile_links.filter(verification_status='verified')):
            service = self.track(link.platform)
            try: url = validate_public_url(service.slug,link.url)
            except ValidationError: continue
            profiles.append({'service':service.slug,'label':text(service.name),'url':url})
        artist = {'id':public_id('artist',a.pk),'name':text(a.name),'slug':a.slug,'bio_short':text(a.bio_short),'bio_long':text(a.bio_long),'location':text(a.location),'artwork':self.artwork(ArtistAsset.objects.filter(artist=a)), 'profile_links':profiles}
        recordings, releases, fanlinks = [], [], []
        def date_fields(obj, field):
            value = getattr(obj,field)
            return value.isoformat()[:{'year':4,'month':7,'day':10}.get(obj.date_precision,0)] if value and obj.date_precision != 'unknown' else None
        for r in self.selected['recordings'].values():
            genres = []
            for relation in self.rows(RecordingGenre.objects.filter(recording=r)):
                genre = self.track(relation.genre); genres.append({'name':text(genre.name),'slug':genre.slug})
            recordings.append({
                'id': public_id('recording', r.pk), 'title': text(r.title),
                'version_title': text(r.version_title), 'recording_type': text(r.recording_type),
                'duration_ms': r.duration_ms, 'explicit': r.explicit,
                'original_release_date': date_fields(r, 'original_release_date'), 'date_precision': r.date_precision,
                'artists': self.credits(r), 'genres': genres,
                'artwork': self.artwork(RecordingAsset.objects.filter(recording=r, is_primary=True)),
                'platform_links': self.destinations(r, 'recording'), 'release_ids': [], 'fanlink_ids': [],
                'identifiers': self.identifiers(r, 'recording'), 'labels': self.labels(r, 'recording'),
                'works': self.works(r), 'lyrics': self.lyrics(RecordingAsset.objects.filter(recording=r)),
                'language': text(r.language), 'disambiguation': text(r.disambiguation),
            })
        for r in self.selected['releases'].values():
            tracks = []
            for track in self.rows(r.tracks.all()):
                if str(track.recording_id) not in self.selected['recordings']: raise ValidationError('All release tracks must be selected recordings.')
                tracks.append({'recording_id':public_id('recording',track.recording_id),'disc_number':track.disc_number,'track_number':track.track_number,'display_title':text(track.display_title), 'duration_ms':track.duration_ms if track.duration_ms is not None else self.track(track.recording).duration_ms, 'credit_text':text(track.credit_text), 'mbid':str(track.mbid) if track.mbid else None, 'medium_id':public_id('medium',track.medium_id) if track.medium_id else None})
                next(item for item in recordings if item['id']==public_id('recording',track.recording_id))['release_ids'].append(public_id('release',r.pk))
            genres = []
            for association in self.rows(ReleaseGenre.objects.filter(release=r)):
                genre = self.track(association.genre)
                genres.append({'name': text(genre.name), 'slug': genre.slug})
            media = [
                {'id': public_id('medium', medium.pk), 'position': medium.position,
                 'title': text(medium.title), 'format': text(medium.format)}
                for medium in sorted(self.rows(r.media.all()), key=lambda row: (row.position, str(row.pk)))
            ]
            known_lengths = [track['duration_ms'] for track in tracks if track['duration_ms'] is not None]
            releases.append({
                'id': public_id('release', r.pk), 'title': text(r.title), 'release_type': text(r.release_type),
                'release_date': date_fields(r, 'release_date'), 'date_precision': r.date_precision,
                'artists': self.credits(r),
                'artwork': self.artwork(ReleaseAsset.objects.filter(release=r, is_primary=True)),
                'tracks': sorted(tracks, key=lambda track: (track['disc_number'], track['track_number'])),
                'platform_links': self.destinations(r, 'release'), 'fanlink_ids': [],
                'identifiers': self.identifiers(r, 'release'), 'labels': self.labels(r, 'release'), 'genres': genres,
                'lyrics': self.lyrics(ReleaseAsset.objects.filter(release=r)),
                'edition': text(r.edition), 'country': text(r.country), 'language': text(r.language),
                'script': text(r.script), 'packaging': text(r.packaging), 'barcode': text(r.barcode),
                'catalog_number': text(r.catalog_number), 'disambiguation': text(r.disambiguation),
                'duration_ms': sum(known_lengths) if known_lengths else None,
                'duration_incomplete': not tracks or len(known_lengths) != len(tracks),
                'release_group': self.release_group(r), 'media': media,
            })
        for f in self.selected['fanlinks'].values():
            kind = 'recording' if f.recording_id else 'release'; target = f.recording_id or f.release_id
            if str(target) not in self.selected[kind+'s']: raise ValidationError('Fanlink target must be selected.')
            item = next(item for item in (recordings if kind=='recording' else releases) if item['id']==public_id(kind,target))
            accepted = {link['url']:link for link in item['platform_links']}
            destinations = []
            for destination in self.rows(f.destinations.filter(enabled=True)):
                platform = self.track(destination.platform_object)
                if platform.url not in accepted: raise ValidationError('Fanlink destinations require accepted target matches.')
                link = dict(accepted[platform.url]); link['label'] = text(destination.label) or link['label']; destinations.append((destination.sort_order,link))
            item['fanlink_ids'].append(public_id('fanlink',f.pk))
            fanlinks.append({'id':public_id('fanlink',f.pk),'slug':f.slug,'title':text(f.title),'target_type':kind,'target_id':item['id'],'artwork':item['artwork'],'destinations':[link for _,link in sorted(destinations,key=lambda pair:(pair[0],pair[1]['url']))]})
        payload = {'artist':artist,'recordings':recordings,'releases':releases,'fanlinks':fanlinks}
        validate_payload(payload)
        return CatalogPreview(str(self.website.pk),self.selection,payload,digest(self.sources),digest(payload),tuple(sorted(self.artwork_versions)))


def preview_catalog(actor, website, selection=None):
    _staff(actor)
    return Builder(website, selection or {}).build()


@transaction.atomic
def approve_catalog(actor, website, preview):
    from publishing.models import Website, ApprovedCatalogRevision, WebsiteApprovedState, ApprovedArtwork
    _staff(actor)
    website = Website.objects.select_for_update().get(pk=website.pk)
    if not isinstance(preview,CatalogPreview) or preview.website_id != str(website.pk): raise ValidationError('Invalid review preview.')
    current = Builder(website,preview.selection,lock=True).build()
    if current != preview: raise ValidationError('Preview is stale; review the current content again.')
    maximum = ApprovedCatalogRevision.objects.filter(website=website).aggregate(value=Max('content_version'))['value'] or 0
    revision = ApprovedCatalogRevision.objects.create(website=website, content_version=maximum+1, approver=actor, schema_version=SCHEMA_VERSION, checksum=current.checksum, source_checksum=current.source_checksum, payload=current.payload, selection=current.selection)
    for version_id in current.artwork_versions: ApprovedArtwork.objects.create(revision=revision, version_id=version_id, public_id=public_id('artwork',version_id))
    WebsiteApprovedState.objects.update_or_create(website=website,defaults={'active_revision':revision,'approved_slug':current.payload['artist']['slug']})
    from provenance.models import AuditEvent
    AuditEvent.objects.create(organization=website.organization,user=actor,action='public_catalog.approve',target_label='Approved catalog revision',change_json={'website_id':str(website.pk),'content_version':revision.content_version})
    return revision


@transaction.atomic
def revoke_catalog(actor, website):
    from publishing.models import Website, WebsiteApprovedState
    _staff(actor)
    Website.objects.select_for_update().get(pk=website.pk)
    states=WebsiteApprovedState.objects.filter(website=website)
    state=states.select_related('active_revision').first()
    content_version=state.active_revision.content_version if state and state.active_revision else None
    states.update(active_revision=None)
    from provenance.models import AuditEvent
    AuditEvent.objects.create(organization=website.organization,user=actor,action='public_catalog.revoke',target_label='Withdraw approved catalog',change_json={'website_id':str(website.pk),'content_version':content_version})


def revoke_for_resource(resource):
    """Call within the transaction archiving content or withdrawing asset rights."""
    from publishing.models import WebsiteApprovedState
    key = {'recording':'recordings','release':'releases','asset':'artwork'}.get(resource._meta.model_name)
    for state in WebsiteApprovedState.objects.select_for_update().filter(active_revision__isnull=False).select_related('website','active_revision'):
        selected = key and str(resource.pk) in state.active_revision.selection.get(key,[])
        if resource._meta.model_name == 'asset': selected = selected or str(resource.pk) in state.active_revision.selection.get('lyrics', [])
        if resource._meta.model_name == 'musicalwork': selected = str(resource.pk) in state.active_revision.selection.get('works', [])
        own = resource._meta.model_name=='artist' and resource.pk==state.website.artist_id
        site = resource._meta.model_name=='website' and resource.pk==state.website_id
        fanlink = resource._meta.model_name=='fanlinkpage' and str(resource.pk) in state.active_revision.selection.get('fanlinks',[])
        if selected or own or site or fanlink:
            state.active_revision=None; state.save(update_fields=['active_revision'])
