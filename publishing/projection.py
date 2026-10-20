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
SCHEMA_VERSION = 1
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


def _keys(value, kind):
    if not isinstance(value, dict) or set(value) != KEYS[kind]:
        raise ValidationError(f'Invalid public {kind} fields.')
    integer_fields = {'billing_order','disc_number','track_number','width','height','duration_ms'}
    list_fields = {'profile_links','artists','genres','platform_links','release_ids','fanlink_ids','tracks','destinations'}
    nullable = {'original_release_date','release_date','duration_ms','explicit','artwork'}
    for key, item in value.items():
        if item is None and key in nullable: continue
        if key == 'artwork':
            if not isinstance(item,dict): raise ValidationError('Invalid artwork.')
        elif key in list_fields:
            if not isinstance(item,list): raise ValidationError('Invalid public array.')
        elif key in integer_fields:
            if type(item) is not int or item < (0 if key in {'billing_order','duration_ms'} else 1): raise ValidationError('Invalid public integer.')
        elif key == 'explicit':
            if type(item) is not bool: raise ValidationError('Invalid public explicit flag.')
        elif not isinstance(item,str) or text(item) != item:
            raise ValidationError('Public text must be plain text.')
        if key in {'id','recording_id','target_id'} or key in {'release_ids','fanlink_ids'}:
            try:
                for identifier in item if isinstance(item,list) else [item]: UUID(identifier)
            except (ValueError,TypeError,AttributeError): raise ValidationError('Invalid public identifier.') from None
    return value


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {'artist','recordings','releases','fanlinks'}:
        raise ValidationError('Invalid public payload fields.')
    _keys(payload['artist'], 'artist')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',payload['artist']['slug']): raise ValidationError('Invalid artist slug.')
    def artwork(value):
        if value is not None:
            _keys(value, 'artwork')
            expected = rf"/api/public/v1/artists/{re.escape(payload['artist']['slug'])}/artwork/[0-9a-f-]{{36}}/"
            if not re.fullmatch(expected, value['url']): raise ValidationError('Invalid public artwork URL.')
            if value['mime_type'] != 'image/webp': raise ValidationError('Invalid public artwork type.')
    def links(items, kind):
        if not isinstance(items,list): raise ValidationError('Invalid public links.')
        for item in items:
            _keys(item, kind)
            validate_public_url(item['service'], item['url'])
    artwork(payload['artist']['artwork'])
    links(payload['artist']['profile_links'], 'profile')
    for kind, plural in [('recording','recordings'), ('release','releases'), ('fanlink','fanlinks')]:
        if not isinstance(payload[plural], list): raise ValidationError('Invalid public collection.')
        seen = set()
        for item in payload[plural]:
            _keys(item, kind); artwork(item['artwork'])
            if item['id'] in seen: raise ValidationError('Duplicate public identifier.')
            seen.add(item['id'])
            if kind == 'fanlink':
                links(item['destinations'], 'destination')
                if item['target_type'] not in ('recording','release') or not re.fullmatch(r'[a-zA-Z0-9_-]+',item['slug']): raise ValidationError('Invalid fanlink target or slug.')
                continue
            links(item['platform_links'], 'destination')
            for credit in item['artists']: _keys(credit, 'credit')
            precision = item['date_precision']
            if precision not in ('unknown','year','month','day'): raise ValidationError('Invalid date precision.')
            date_value = item['original_release_date' if kind == 'recording' else 'release_date']
            pattern = {'year': r'\d{4}', 'month': r'\d{4}-\d{2}', 'day': r'\d{4}-\d{2}-\d{2}'}.get(precision)
            if date_value is not None:
                if not pattern or not re.fullmatch(pattern,date_value): raise ValidationError('Invalid date value.')
                try: date.fromisoformat(date_value + {'year':'-01-01','month':'-01','day':''}[precision])
                except ValueError: raise ValidationError('Invalid date value.') from None
            if kind == 'recording':
                for genre in item['genres']: _keys(genre, 'genre')
            else:
                for track in item['tracks']: _keys(track, 'track')
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


def normalize_selection(selection):
    if not isinstance(selection,dict) or set(selection)-{'recordings','releases','fanlinks','artwork'}:
        raise ValidationError('Invalid publication selection.')
    normalized={}
    for key in ('recordings','releases','fanlinks','artwork'):
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
        self.lock = lock
        self.sources = {}
        self.website = self.track(type(website).objects.get(pk=website.pk))
        self.artist = self.track(self.website.artist)
        if self.website.organization_id != self.artist.organization_id or getattr(self.artist, 'archived_at', None) or getattr(self.website, 'archived_at', None): raise ValidationError('Website is unavailable.')
        selection=normalize_selection(selection)
        self.selection = {}
        self.selected = {}
        self.artwork_versions = set()
        for key, model in [('recordings',Recording),('releases',Release),('fanlinks',FanlinkPage),('artwork',Asset)]:
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
                    grant = ArtistResourceGrant.objects.filter(artist=self.artist, **{('asset' if key == 'artwork' else key[:-1]): row}).first()
                    if not grant: raise ValidationError('Selection requires an explicit artist grant.')
                    self.track(grant)
                    if key != 'artwork' and row.status != 'approved': raise ValidationError('Selected content requires approval.')

    def track(self, obj):
        if self.lock:
            obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        self.sources[f'{obj._meta.label}:{obj.pk}'] = {f.attname: f.value_from_object(obj) for f in obj._meta.concrete_fields}
        return obj

    def rows(self, queryset):
        from portal.querysets import active_rows
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
            result.append({'id':public_id('artist',artist.pk),'name':text(artist.name),'role':text(credit.role),'billing_order':credit.billing_order})
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

    def build(self):
        from catalog.models import ArtistAsset, RecordingAsset, RecordingGenre, ReleaseAsset
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
            recordings.append({'id':public_id('recording',r.pk),'title':text(r.title),'version_title':text(r.version_title),'recording_type':text(r.recording_type),'duration_ms':r.duration_ms,'explicit':r.explicit,'original_release_date':date_fields(r,'original_release_date'),'date_precision':r.date_precision,'artists':self.credits(r),'genres':genres,'artwork':self.artwork(RecordingAsset.objects.filter(recording=r,is_primary=True)),'platform_links':self.destinations(r,'recording'),'release_ids':[],'fanlink_ids':[]})
        for r in self.selected['releases'].values():
            tracks = []
            for track in self.rows(r.tracks.all()):
                if str(track.recording_id) not in self.selected['recordings']: raise ValidationError('All release tracks must be selected recordings.')
                tracks.append({'recording_id':public_id('recording',track.recording_id),'disc_number':track.disc_number,'track_number':track.track_number,'display_title':text(track.display_title)})
                next(item for item in recordings if item['id']==public_id('recording',track.recording_id))['release_ids'].append(public_id('release',r.pk))
            releases.append({'id':public_id('release',r.pk),'title':text(r.title),'release_type':text(r.release_type),'release_date':date_fields(r,'release_date'),'date_precision':r.date_precision,'artists':self.credits(r),'artwork':self.artwork(ReleaseAsset.objects.filter(release=r,is_primary=True)),'tracks':sorted(tracks,key=lambda t:(t['disc_number'],t['track_number'])),'platform_links':self.destinations(r,'release'),'fanlink_ids':[]})
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
    revision = ApprovedCatalogRevision.objects.create(website=website, content_version=maximum+1, approver=actor, checksum=current.checksum, source_checksum=current.source_checksum, payload=current.payload, selection=current.selection)
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
        own = resource._meta.model_name=='artist' and resource.pk==state.website.artist_id
        site = resource._meta.model_name=='website' and resource.pk==state.website_id
        fanlink = resource._meta.model_name=='fanlinkpage' and str(resource.pk) in state.active_revision.selection.get('fanlinks',[])
        if selected or own or site or fanlink:
            state.active_revision=None; state.save(update_fields=['active_revision'])
