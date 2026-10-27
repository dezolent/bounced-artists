"""Conservative identity resolution and reversible, hash-guarded catalog cleanup.

Source payloads, import mappings and immutable publication snapshots are never
rewritten. Duplicate parents and colliding child rows are retained for audit.
"""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.models import Q
from django.utils import timezone

from artists.models import ArtistResourceGrant
from catalog.models import Asset, Recording, RecordingAsset, RecordingIdentifier
from portal.querysets import active_rows
from provenance.models import AuditEvent, EntityTarget, FieldObservation, ReviewCandidate, ReviewItem


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, cls=DjangoJSONEncoder, separators=(',', ':')).encode()).hexdigest()


def title_key(value):
    """Ignore feature-credit typography, never live/remix/edit/version labels."""
    value = unicodedata.normalize('NFKC', str(value or '')).casefold().strip()
    value = re.sub(r'\s*[\[(]\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]', '', value)
    # An unbracketed credit can be followed by a meaningful version suffix.
    # Keep that suffix; it must never be absorbed into the artist credit.
    value = re.sub(r'\s+(?:feat\.?|ft\.?|featuring)\s+.*?(?=\s+-\s+|$)', '', value)
    return re.sub(r'[^\w]+', ' ', value).strip()


def _isrc(payload):
    external = payload.get('external_ids')
    external = external if isinstance(external, dict) else {}
    value = payload.get('isrc') or external.get('isrc') or ''
    return re.sub(r'[-\s]', '', str(value)).upper()


def _duration(payload):
    value = payload.get('duration_ms', payload.get('duration'))
    return value if type(value) is int and value > 0 else None


def _recording_titles(recording):
    values = {title_key(recording.title)}
    values.update(title_key(value) for value in FieldObservation.objects.filter(
        target__recording=recording, field_name='title').values_list('value_json', flat=True) if isinstance(value, str))
    return values


def _same_version(recording, payload):
    version = payload.get('version_title') or ''
    return title_key(recording.version_title) == title_key(version)


def platform_identity_keys(obj):
    """Only native IDs and exact platform permalinks, excluding share tokens."""
    from urllib.parse import urlsplit, urlunsplit
    from portal.recording_metrics import locator
    slug,kind,external_id,url=locator(obj)
    keys=set()
    if external_id and kind in {'track','video','post','music'}:
        keys.add(('native',slug,kind,str(external_id)))
    if slug=='soundcloud' and url:
        parsed=urlsplit(url)
        parts=parsed.path.strip('/').split('/')
        # SoundCloud secret links append /s-<token> to the same track path.
        if parsed.hostname=='soundcloud.com' and (len(parts)==2 or
                len(parts)==3 and re.fullmatch(r's-[A-Za-z0-9]+',parts[2])):
            path='/'+ '/'.join(parts[:2])
            keys.add(('permalink',slug,'track',urlunsplit(('https','soundcloud.com',path,'',''))))
    elif url and kind in {'track','video','post','music'} and not external_id:
        # Other services need their parsed native locator; do not equate a
        # query-free YouTube URL or Apple album with individual recordings.
        keys.add(('url',slug,kind,url))
    return keys


def _recording_platform_keys(recording):
    keys=set()
    matches=active_rows(recording.recordingplatformmatch_set.filter(review_status='accepted',
        relationship_type='full_recording')).select_related('platform_object__service')
    for match in matches:
        if active_rows(type(match.platform_object).objects.filter(pk=match.platform_object_id)).exists():
            keys.update(platform_identity_keys(match.platform_object))
    return keys


def resolve_candidate(artist, payload):
    """Return (safe unique recording or None, likely candidates).

    Attribution must already be checked by the collector. Title, artist, version,
    duration and identifier/release corroboration are all required for automatic
    association. A likely title without enough evidence is held for review.
    """
    key = title_key(payload.get('name') or payload.get('title'))
    if not key:
        return None, []
    candidates = []
    proven = []
    rows = Recording.objects.filter(organization=artist.organization, archived_at__isnull=True).exclude(status='archived').filter(
        Q(credits__artist=artist) | Q(artist_grants__artist=artist)).distinct().order_by('created_at', 'pk')
    for recording in rows:
        if key not in _recording_titles(recording) or not _same_version(recording, payload):
            continue
        candidates.append(recording)
        if not active_rows(Recording.objects.filter(pk=recording.pk)).exists():
            continue
        if not active_rows(ArtistResourceGrant.objects.filter(artist=artist, recording=recording)).exists():
            continue
        duration = _duration(payload)
        if not duration or not recording.duration_ms or abs(duration-recording.duration_ms) > 2000:
            continue
        isrc = _isrc(payload)
        identifiers = active_rows(RecordingIdentifier.objects.filter(recording=recording, scheme__iexact='isrc')).exclude(verification_status='rejected')
        same_identifier = bool(isrc and any(_isrc({'isrc':value}) == isrc for value in identifiers.values_list('value', flat=True)))
        album = payload.get('album')
        album = album if isinstance(album, dict) else {}
        release_key = title_key(album.get('name') or album.get('title'))
        # A single title repeated as its album name is not independent evidence.
        same_release = bool(release_key and release_key != key and any(title_key(value) == release_key for value in
            recording.release_tracks.filter(archived_at__isnull=True, release__archived_at__isnull=True).values_list('release__title', flat=True)))
        if same_identifier or same_release:
            proven.append(recording)
    return (proven[0] if len(proven) == 1 and len(candidates) == 1 else None), candidates


def hold_candidate_review(source, candidates):
    """One operator decision per original source identity; never auto-resolve it."""
    prior = ReviewItem.objects.filter(issue_type='recording_identity_ambiguous',
        source_record__organization=source.organization, source_record__service=source.service,
        source_record__object_type=source.object_type, source_record__external_id=source.external_id).order_by('created_at', 'pk').first()
    if prior:
        return prior
    item = ReviewItem.objects.create(source_record=source, issue_type='recording_identity_ambiguous')
    for recording in candidates:
        target = EntityTarget.objects.filter(organization=source.organization, recording=recording).order_by('created_at', 'pk').first()
        if target is None:
            target = EntityTarget.objects.create(organization=source.organization, recording=recording)
        ReviewCandidate.objects.get_or_create(review_item=item, target=target)
    return item


def recording_identity_map(recordings):
    """Batch source aliases for already authorized rows, with two queries.

    Only audited edges and retained archived rows in the same organization count.
    This helper does not grant access to the supplied rows or their originals.
    """
    recordings=list(recordings)
    result={str(row.pk):{str(row.pk)} for row in recordings}
    if not recordings:
        return result
    organizations={str(row.organization_id) for row in recordings}
    archived_by_org=defaultdict(set)
    for pk,organization_id in Recording.objects.filter(organization_id__in=organizations,
            archived_at__isnull=False).values_list('pk','organization_id'):
        archived_by_org[str(organization_id)].add(str(pk))
    edges_by_org=defaultdict(list)
    for organization_id,merges in AuditEvent.objects.filter(organization_id__in=organizations,
            action='catalog.reconcile').values_list('organization_id','change_json__recording_merges'):
        if isinstance(merges,list):
            edges_by_org[str(organization_id)].extend(edge for edge in merges if isinstance(edge,dict) and
                edge.get('organization_id')==str(organization_id))
    for recording in recordings:
        aliases=result[str(recording.pk)]
        organization=str(recording.organization_id)
        changed=True
        while changed:
            changed=False
            for edge in edges_by_org[organization]:
                duplicate=edge.get('duplicate_id')
                if edge.get('canonical_id') in aliases and duplicate in archived_by_org[organization] and duplicate not in aliases:
                    aliases.add(duplicate)
                    changed=True
    return result


def recording_identity_ids(recording):
    """Immutable source UUID aliases for an already authorized canonical row."""
    return recording_identity_map([recording])[str(recording.pk)]


# Every current FK to Recording/Asset is inspected, including future additions.
# Unsupported new relationship models stop cleanup instead of silently losing it.
RECORDING_LINKS = {
    'catalog.recordingartist','catalog.recordingidentifier','catalog.recordingrelation',
    'catalog.releasetrack','catalog.recordinggenre','catalog.recordinglabel',
    'catalog.recordingasset','catalog.recordingplatformmatch','artists.artistresourcegrant',
    'provenance.entitytarget','rights.recordingwork','analytics.audioanalysis',
    'analytics.playlistappearance','publishing.fanlinkpage',
}
ASSET_LINKS = {
    'catalog.artistasset','catalog.releaseasset','catalog.recordingasset',
    'artists.artistresourcegrant','provenance.sourcerecord','analytics.audioanalysis',
    'analytics.reportimport','publishing.seorecord','publishing.websitepublication',
    'catalog.assetversion',
}


def relation_census(model):
    return sorted((rel.related_model._meta.label_lower, rel.field.name) for rel in model._meta.related_objects if not rel.many_to_many)


def _state_models():
    labels = {label for model in (Recording, Asset) for label, _ in relation_census(model)}
    labels.update({'catalog.recording','catalog.asset','catalog.platformobject','portal.editorialchange','portal.operationremoval',
        'provenance.fieldobservation','provenance.editorialoverride','publishing.website'})
    return [apps.get_model(label) for label in sorted(labels)]


def state_fingerprint():
    """Hash table state privately; the plan contains no payloads or credentials."""
    result = {}
    for model in _state_models():
        rows_query = model.objects.order_by('pk')
        if model._meta.label_lower == 'provenance.sourcerecord':
            # Immutable collector payloads can be hundreds of MB; their receipt
            # hash and timestamps guard state without loading private archives.
            rows_query = rows_query.values('id','organization_id','service_id','private_asset_id','payload_hash','updated_at')
        else:
            rows_query = rows_query.values()
        rows = list(rows_query)
        result[model._meta.label_lower] = {'count':len(rows), 'sha256':digest(rows)}
    return result


def _removed(row):
    return not active_rows(type(row).objects.filter(pk=row.pk)).exists()


def _imported_recording_ids(organization):
    from publishing.models import Website
    result=set()
    for config in Website.objects.filter(organization=organization).values_list('theme_config', flat=True):
        mapping = (config.get('dezolent_import') or {}).get('entity_ids', {}) if isinstance(config,dict) else {}
        result.update(str(value) for key,value in mapping.items() if key.startswith('recording:'))
    return result


def _canonical_rank(row):
    from provenance.collectors.catalog_metadata import edited_fields
    edited=bool(edited_fields(row,EntityTarget.objects.filter(recording=row)))
    return (str(row.pk) not in _imported_recording_ids(row.organization), not edited,
        row.status != 'approved', row.created_at, str(row.pk))


def _pair_evidence(source, canonical):
    if source.organization_id != canonical.organization_id:
        return 'different organizations'
    if not _recording_titles(source).intersection(_recording_titles(canonical)):
        return 'different song titles'
    if source.version_title != canonical.version_title or source.recording_type != canonical.recording_type:
        return 'different versions'
    source_artists = set(source.credits.filter(role='primary').values_list('artist_id',flat=True))
    canonical_artists = set(canonical.credits.filter(role='primary').values_list('artist_id',flat=True))
    if not source_artists or source_artists != canonical_artists:
        return 'different or missing primary artists'
    if source.duration_ms and canonical.duration_ms and abs(source.duration_ms-canonical.duration_ms)>2000:
        return 'duration mismatch'
    ids = lambda row: {_isrc({'isrc':value}) for value in active_rows(row.identifiers.filter(scheme__iexact='isrc')).exclude(verification_status='rejected').values_list('value',flat=True)}
    same_id = bool(ids(source) & ids(canonical))
    releases = lambda row: set(row.release_tracks.filter(archived_at__isnull=True).values_list('release_id',flat=True))
    same_release = bool(releases(source) & releases(canonical))
    same_platform_identity = bool(_recording_platform_keys(source) & _recording_platform_keys(canonical))
    duration = source.duration_ms and canonical.duration_ms and abs(source.duration_ms-canonical.duration_ms)<=2000
    if not duration or not (same_id or same_release or same_platform_identity):
        return 'insufficient independent evidence'
    return ''


def _editorial_conflict(source, canonical):
    if str(source.pk) in _imported_recording_ids(source.organization):
        return 'duplicate is a persisted import identity; keep it canonical'
    if source.archived_at or canonical.archived_at or source.status == 'archived' or canonical.status == 'archived':
        return 'archived recording'
    if _removed(source) or _removed(canonical):
        return 'removed recording'
    # Retain unique source fields on the archived row; never erase an editorial edit.
    from provenance.collectors.catalog_metadata import edited_fields
    targets = EntityTarget.objects.filter(recording=source)
    if edited_fields(source, targets) or source.status != 'draft':
        return 'duplicate has editorial revision or approval; choose it as canonical or review manually'
    for field in ('version_title','language','disambiguation','notes'):
        value = getattr(source,field)
        if value and value != getattr(canonical,field):
            if field == 'language' and not canonical.language and canonical.status == 'draft' and 'language' not in edited_fields(canonical, EntityTarget.objects.filter(recording=canonical)):
                continue
            return f'distinct editorial {field}'
    return ''


def _unique_keys(model):
    keys = []
    for constraint in model._meta.constraints:
        if isinstance(constraint,models.UniqueConstraint) and constraint.fields:
            keys.append(tuple(constraint.fields))
    keys.extend(tuple(fields) for fields in model._meta.unique_together)
    return keys


def _link_actions(model, field_name, duplicate_id, canonical_id, actions, conflicts, virtual):
    """Move noncolliding rows by UUID; retain collisions under archived parents."""
    field = model._meta.get_field(field_name)
    organization_id = str(field.remote_field.model.objects.values_list('organization_id',flat=True).get(pk=canonical_id))
    rows = virtual.setdefault(model._meta.label_lower, list(model.objects.order_by('pk')))
    for row in [row for row in rows if str(getattr(row,field.attname)) == str(duplicate_id)]:
        values = {f.name:getattr(row,f.attname) for f in model._meta.concrete_fields}
        values[field_name] = canonical_id
        if model._meta.label_lower == 'catalog.recordingplatformmatch':
            identity = platform_identity_keys(row.platform_object)
            for other in rows:
                if other.pk == row.pk or str(other.recording_id) != str(canonical_id):
                    continue
                if identity & platform_identity_keys(other.platform_object):
                    if (row.review_status != other.review_status or
                            row.relationship_type != other.relationship_type or
                            _removed(row) != _removed(other)):
                        conflicts.append({'model': model._meta.label_lower, 'id': str(row.pk),
                            'reason': 'conflicting decisions for platform identity aliases'})
        if model._meta.label_lower == 'catalog.recordingrelation' and str(values['from_recording']) == str(values['to_recording']):
            actions.append({'model':model._meta.label_lower,'id':str(row.pk),'retain':'self relation','organization_id':organization_id})
            continue
        collision = None
        for key in _unique_keys(model):
            if field_name not in key:
                continue
            # Conditional primary constraints apply only to primary rows.
            primary_key = key in ((field_name,), (field_name,'purpose')) and hasattr(row,'is_primary')
            if primary_key and not row.is_primary:
                continue
            collision = next((other for other in rows if other.pk != row.pk and
                all(str(getattr(other,model._meta.get_field(name).attname)) == str(values[name]) for name in key) and
                (not primary_key or other.is_primary)), None)
            if collision:
                break
        action = {'model':model._meta.label_lower,'id':str(row.pk),'organization_id':organization_id,'field':field_name,'from':str(duplicate_id),'to':str(canonical_id)}
        if collision:
            # Access and rejection/removal decisions cannot be upgraded by merging.
            sensitive = ('review_status','verification_status','capability')
            if any(hasattr(row,name) and getattr(row,name)!=getattr(collision,name) for name in sensitive) or _removed(row)!=_removed(collision):
                conflicts.append({'model':model._meta.label_lower,'id':str(row.pk),'reason':'colliding access/review/removal decision'})
            if model._meta.label_lower == 'catalog.recordingplatformmatch':
                # Keeping a duplicate match on an archived parent makes the
                # collector see two recording owners for the same object.
                # Hold for explicit resolution instead of breaking enrichment.
                conflicts.append({'model':model._meta.label_lower,'id':str(row.pk),
                    'reason':'duplicate platform match requires decision-preserving resolution'})
            if hasattr(row,'is_primary') and row.is_primary and getattr(collision,'asset_id',None)!=getattr(row,'asset_id',None):
                # Keep canonical cover selection while exposing the other cover as secondary.
                action['updates']={'is_primary':False}
                row.is_primary=False
                setattr(row,field.attname,canonical_id)
                actions.append(action)
            else:
                action['retain']='unique collision'
                action['canonical_child_id']=str(collision.pk)
                actions.append(action)
        else:
            setattr(row,field.attname,canonical_id)
            actions.append(action)


def _asset_signature(asset):
    if asset.revision != 1 or asset.current_version_id or asset.storage_key or asset.source_repository or asset.alt_text or asset.rights_approved or _removed(asset):
        return None
    from portal.models import EditorialChange
    if EditorialChange.objects.filter(model_label='catalog.asset',object_id=str(asset.pk)).exists():
        return None
    if asset.description and not asset.description.startswith('Reported by '):
        return None
    location = ('sha256',asset.sha256) if asset.sha256 else ('url',asset.source_url) if asset.source_url else None
    if location is None:
        return None
    return (str(asset.organization_id), location, asset.mime_type, asset.visibility, asset.rights_basis, asset.width, asset.height)


def build_plan(*, recording_pairs=None, include_assets=True):
    """Read-only plan. Explicit pairs choose direction but never bypass evidence."""
    initial_state = state_fingerprint()
    plan = {'version':1,'recording_merges':[],'asset_merges':[],'actions':[],'conflicts':[],'pending':[],
        'recording_relations':relation_census(Recording),'asset_relations':relation_census(Asset)}
    for label,_ in plan['recording_relations']:
        if label not in RECORDING_LINKS:
            plan['conflicts'].append({'model':label,'reason':'unsupported recording relationship'})
    for label,_ in plan['asset_relations']:
        if label not in ASSET_LINKS:
            plan['conflicts'].append({'model':label,'reason':'unsupported asset relationship'})
    rows = list(Recording.objects.filter(archived_at__isnull=True).exclude(status='archived').order_by('created_at','pk'))
    if recording_pairs is None:
        pairs=[]
        groups=defaultdict(list)
        for row in rows:
            groups[(row.organization_id,title_key(row.title),row.version_title,row.recording_type)].append(row)
        for group in groups.values():
            ordered=sorted(group,key=_canonical_rank)
            for duplicate in ordered[1:]:
                reason = _pair_evidence(duplicate,ordered[0])
                if not reason:
                    pairs.append((str(duplicate.pk),str(ordered[0].pk)))
                else:
                    plan['pending'].append({'duplicate_id':str(duplicate.pk),'candidate_id':str(ordered[0].pk),'reason':reason})
    else:
        pairs = list(recording_pairs)
    source_ids=[str(pair[0]) for pair in pairs]
    target_ids={str(pair[1]) for pair in pairs}
    if len(source_ids)!=len(set(source_ids)) or set(source_ids)&target_ids:
        plan['conflicts'].append({'reason':'duplicate or chained merge mappings'})
    recording_map={}
    virtual={}
    canonical_languages={}
    for duplicate_id,canonical_id in pairs:
        try:
            duplicate=Recording.objects.get(pk=duplicate_id); canonical=Recording.objects.get(pk=canonical_id)
        except (Recording.DoesNotExist,ValueError):
            plan['conflicts'].append({'duplicate_id':str(duplicate_id),'reason':'missing recording'})
            continue
        if str(canonical.pk) in canonical_languages:
            canonical.language = canonical_languages[str(canonical.pk)]
        reason=_pair_evidence(duplicate,canonical) or _editorial_conflict(duplicate,canonical)
        if reason:
            plan['conflicts'].append({'duplicate_id':str(duplicate_id),'reason':reason})
            continue
        plan['recording_merges'].append({'duplicate_id':str(duplicate.pk),'canonical_id':str(canonical.pk),'organization_id':str(canonical.organization_id)})
        if duplicate.language and not canonical.language:
            plan['actions'].append({'model':'catalog.recording', 'id':str(canonical.pk), 'organization_id':str(canonical.organization_id),
                'field':'language', 'from':'', 'to':duplicate.language, 'copy_value':True})
            canonical.language = duplicate.language
            canonical_languages[str(canonical.pk)] = duplicate.language
        recording_map[str(duplicate.pk)]=str(canonical.pk)
        for label,field in plan['recording_relations']:
            if label in RECORDING_LINKS:
                _link_actions(apps.get_model(label),field,duplicate.pk,canonical.pk,plan['actions'],plan['conflicts'],virtual)
    if include_assets:
        groups=defaultdict(list)
        for link in RecordingAsset.objects.filter(purpose='cover',asset__archived_at__isnull=True).select_related('asset').order_by('asset__created_at','asset_id'):
            signature=_asset_signature(link.asset)
            if signature:
                owner=recording_map.get(str(link.recording_id),str(link.recording_id))
                key=(owner,signature)
                if link.asset_id not in [asset.pk for asset in groups[key]]:
                    groups[key].append(link.asset)
        # Join overlapping cover groups first, so shared assets never produce
        # chained archive targets in a single plan.
        assets = {str(asset.pk):asset for group in groups.values() for asset in group}
        parents = {key:key for key in assets}
        def find(key):
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key
        for group in groups.values():
            for asset in group[1:]:
                parents[find(str(asset.pk))] = find(str(group[0].pk))
        components=defaultdict(list)
        for key,asset in assets.items():
            components[find(key)].append(asset)
        for group in components.values():
            group.sort(key=lambda asset:(asset.created_at,str(asset.pk)))
            canonical=group[0]
            for duplicate in group[1:]:
                # Uploaded versions/private source evidence/publications are immutable.
                protected=False
                for label,field in plan['asset_relations']:
                    if label in {'catalog.assetversion','provenance.sourcerecord','publishing.websitepublication'} and apps.get_model(label).objects.filter(**{field:duplicate.pk}).exists():
                        protected=True
                if protected:
                    continue
                plan['asset_merges'].append({'duplicate_id':str(duplicate.pk),'canonical_id':str(canonical.pk),'organization_id':str(canonical.organization_id)})
                for label,field in plan['asset_relations']:
                    if label in ASSET_LINKS:
                        _link_actions(apps.get_model(label),field,duplicate.pk,canonical.pk,plan['actions'],plan['conflicts'],virtual)
    plan['state']=state_fingerprint()
    if plan['state'] != initial_state:
        raise ValueError('Catalog changed during planning; retry the dry run')
    plan['plan_hash']=digest(plan)
    return plan


def _counts():
    return {'recordings':Recording.objects.count(),'active_recordings':Recording.objects.filter(archived_at__isnull=True).exclude(status='archived').count(),
        'assets':Asset.objects.count(),'active_assets':Asset.objects.filter(archived_at__isnull=True).count(),
        'source_records':apps.get_model('provenance.SourceRecord').objects.count(),
        'field_observations':FieldObservation.objects.count(),
        'artist_grants':ArtistResourceGrant.objects.count(),
        'artist_memberships':apps.get_model('artists.ArtistMembership').objects.count(),
        'platform_matches':apps.get_model('catalog.RecordingPlatformMatch').objects.count(),
        'review_items':ReviewItem.objects.count(),
        'table_rows':{model._meta.label_lower:model.objects.count() for model in _state_models()}}


@transaction.atomic
def apply_plan(plan, *, expected_hash, rollback=False):
    """One transaction, stale-state guard, retained originals and repeat receipt."""
    unsigned={key:value for key,value in plan.items() if key!='plan_hash'}
    if expected_hash!=plan.get('plan_hash') or digest(unsigned)!=expected_hash:
        raise ValueError('Plan hash mismatch')
    if plan.get('conflicts'):
        raise ValueError('Plan has conflicts; no changes applied')
    if connection.vendor!='postgresql':
        raise ValueError('Reconciliation requires PostgreSQL locking')
    # Block writes while checking the complete FK census and guarded snapshot.
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '30s'")
        cursor.execute('SELECT pg_advisory_xact_lock(%s)',[702731415])
        for model in _state_models():
            cursor.execute('LOCK TABLE '+connection.ops.quote_name(model._meta.db_table)+' IN SHARE ROW EXCLUSIVE MODE')
    if AuditEvent.objects.filter(action='catalog.reconcile',change_json__plan_hash=expected_hash).exists():
        return {'already_applied':True,'plan_hash':expected_hash}
    if state_fingerprint()!=plan['state']:
        raise ValueError('Catalog changed since the dry run; regenerate the plan')
    result={'already_applied':False,'plan_hash':expected_hash,'before':_counts(),'recordings_archived':0,'assets_archived':0,'links_moved':0,'links_retained':0,'fields_filled':0}
    for action in plan['actions']:
        if action.get('retain'):
            result['links_retained']+=1
            continue
        model=apps.get_model(action['model'])
        field=model._meta.get_field(action['field'])
        if action.get('copy_value'):
            if model.objects.filter(pk=action['id'], **{field.attname: action['from']}).update(**{field.attname: action['to'], 'updated_at': timezone.now(), 'revision': models.F('revision') + 1}) != 1:
                raise ValueError('Recording field changed during apply')
            result['fields_filled'] += 1
            continue
        values={field.attname:action['to'],**action.get('updates',{})}
        if model.objects.filter(pk=action['id'],**{field.attname:action['from']}).update(**values)!=1:
            raise ValueError('Relationship changed during apply')
        result['links_moved']+=1
    now=timezone.now()
    for key,model,count_key in [('recording_merges',Recording,'recordings_archived'),('asset_merges',Asset,'assets_archived')]:
        for merge in plan[key]:
            values={'archived_at':now,'updated_at':now,'revision':models.F('revision')+1}
            if model is Recording:
                values['status']='archived'
            model.objects.filter(pk=merge['duplicate_id']).update(**values)
            result[count_key]+=1
    result['after']=_counts()
    for organization in {merge['organization_id'] for key in ('recording_merges','asset_merges') for merge in plan[key]}:
        AuditEvent.objects.create(organization_id=organization,action='catalog.reconcile',target_label='catalog.recording',
            change_json={'plan_hash':expected_hash,'recording_merges':[m for m in plan['recording_merges'] if m['organization_id']==organization],
                'asset_merges':[m for m in plan['asset_merges'] if m['organization_id']==organization],
                'actions':[action for action in plan['actions'] if action['organization_id']==organization],'counts':result})
    if rollback:
        transaction.set_rollback(True)
        result['rolled_back']=True
    return result
