"""Source-backed performer credits; identity and editorial decisions stay private.

Spotify's headline artist array has order, but not necessarily roles. Explicit
credit contributors supply those roles. Neither names nor array position grant
identity, a primary credit, workspace access, or collection authorization.
"""
import re
from datetime import timedelta
from uuid import UUID, NAMESPACE_URL, uuid5

from django.contrib.admin.models import LogEntry
from django.db import transaction

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant
from catalog.models import ArtistPlatformMatch, PlatformObject, Recording, RecordingArtist, RecordingPlatformMatch
from portal.models import EditorialChange, OperationRemoval
from portal.querysets import active_rows
from provenance.models import AuditEvent, EntityTarget, FieldObservation


ROLES = {'mainartist': 'primary', 'primaryartist': 'primary', 'primary': 'primary',
         'featuredartist': 'featured', 'featured': 'featured',
         'guestartist': 'guest', 'guest': 'guest'}
FIELDS = ('id', 'artist_id', 'role', 'billing_order', 'credited_as', 'join_phrase')


def reported_credits(payload, workspace_ids, workspace_name):
    artists = payload.get('artists')
    if not isinstance(artists, list) or not 1 <= len(artists) <= 100:
        return None
    ids, result = set(), []
    credits = payload.get('credits')
    contributors = credits.get('contributors', []) if isinstance(credits, dict) else []
    if not isinstance(contributors, list):
        return None
    roles = {}
    for person in contributors:
        if not isinstance(person, dict):
            continue
        role = ROLES.get(re.sub(r'[^a-z]', '', str(person.get('role', '')).casefold()))
        identity = person.get('artist_id')
        if role and isinstance(identity, str) and identity:
            roles.setdefault(identity, set()).add(role)
    for index, artist in enumerate(artists, 1):
        if not isinstance(artist, dict):
            return None
        identity = artist.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9]{1,255}', identity) or identity in ids:
            return None
        ids.add(identity)
        name = artist.get('name') or (workspace_name if identity in workspace_ids else '')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            return None
        choices = roles.get(identity, set())
        if len(choices) > 1:
            return None
        role = next(iter(choices)) if choices else 'primary' if len(artists) == 1 else 'performer'
        result.append({'external_id': identity, 'name': name.strip(), 'role': role, 'billing_order': index})
    return result if ids & workspace_ids else None


def _snapshot(recording):
    return [{key: str(value) if key in {'id', 'artist_id'} else value for key, value in row.items()}
            for row in recording.credits.order_by('pk').values(*FIELDS)]


def _protected(recording, before):
    from publishing.models import Website
    ids = [row['id'] for row in before]
    protected_ids = {*ids, str(recording.pk)}
    for mapping in Website.objects.filter(organization=recording.organization).values_list(
            'theme_config__dezolent_import__entity_ids', flat=True):
        if isinstance(mapping, dict) and any(isinstance(value, str) and value in protected_ids
                for key, value in mapping.items() if key.startswith(('recording:', 'recording_credit:'))):
            return True
    if EditorialChange.objects.filter(model_label='catalog.recordingartist', object_id__in=ids).exists():
        return True
    if OperationRemoval.objects.filter(model_label='catalog.recordingartist', object_id__in=ids).exists():
        return True
    if AuditEvent.objects.filter(organization=recording.organization, target_label='catalog.recordingartist',
            change_json__object_id__in=ids, action__startswith='portal.operations.').exists():
        return True
    if LogEntry.objects.filter(content_type__app_label='catalog', content_type__model='recordingartist', object_id__in=ids).exists():
        return True
    # Django admin inline edits are attached to the parent. Conservatively keep
    # any parent admin edit, rather than trying to parse arbitrary old messages.
    return LogEntry.objects.filter(content_type__app_label='catalog', content_type__model='recording', object_id=str(recording.pk)).exists()


def _legacy(recording, conn, before):
    if len(before) != 1:
        return False
    credit = before[0]
    if (credit['artist_id'] != str(conn.artist_id) or credit['role'] != 'primary' or
            credit['billing_order'] != 1 or credit['credited_as'] or credit['join_phrase']):
        return False
    original = recording.credits.get(pk=credit['id'])
    if not 0 <= (original.created_at - recording.created_at).total_seconds() < 5:
        return False
    # Discovery evidence and its accepted match distinguish the old collector
    # default from a title match, imported credit, or manually created primary.
    return FieldObservation.objects.filter(target__recording=recording, field_name='title',
        source_record__organization=conn.organization, source_record__service=conn.service,
        source_record__sync_run__sync_job__connection=conn,
        source_record__object_type__in=['track-details', 'spotify-track'],
        source_record__created_at__lte=original.created_at,
        created_at__gte=recording.created_at,
        created_at__lte=original.created_at + timedelta(seconds=5),
    ).exists() and RecordingPlatformMatch.objects.filter(recording=recording,
        platform_object__service=conn.service, review_status='accepted',
        match_method='verified_platform_id').exists()


def _artist_identity(conn, external_id):
    from .providers import profile_locator
    from .http import CollectionError
    objects = PlatformObject.objects.filter(service=conn.service, object_type='artist', external_id=external_id)
    if objects.exists() and active_rows(objects).count() != objects.count():
        raise ValueError('artist_identity_decision')
    matches = list(ArtistPlatformMatch.objects.filter(platform_object__service=conn.service,
        platform_object__object_type='artist', platform_object__external_id=external_id,
        artist__organization=conn.organization).select_related('artist', 'platform_object'))
    active = set(active_rows(ArtistPlatformMatch.objects.filter(pk__in=[m.pk for m in matches])).values_list('pk', flat=True))
    candidates = set()
    for match in matches:
        if (match.pk not in active or match.review_status != 'accepted' or match.artist.archived_at or
                not active_rows(Artist.objects.filter(pk=match.artist_id)).exists()):
            raise ValueError('artist_identity_decision')
        if not active_rows(PlatformObject.objects.filter(pk=match.platform_object_id)).exists():
            raise ValueError('artist_identity_decision')
        candidates.add(match.artist_id)
    for profile in active_rows(ArtistProfileLink.objects.filter(platform=conn.service,
            artist__organization=conn.organization, artist__archived_at__isnull=True,
            verification_status='verified')):
        try:
            identity = profile_locator('spotify', profile.url)
        except (CollectionError, ValueError, TypeError):
            continue
        if identity == external_id:
            candidates.add(profile.artist_id)
    if len(candidates) > 1:
        raise ValueError('ambiguous_artist_identity')
    if candidates:
        return next(iter(candidates))
    return uuid5(NAMESPACE_URL, f'bounced-artists:{conn.organization_id}:spotify:artist:{external_id}')


def plan_recording_credits(recording, conn, source, *, repair_legacy=False, initial=False):
    from .catalog import _profiles, _owned, _identities
    result = {'recording_id': str(recording.pk), 'title': recording.title,
              'source_id': str(source.pk), 'source_hash': source.payload_hash,
              'source_updated_at': source.updated_at.isoformat(), 'status': 'unauthorized'}
    if (conn.service.slug != 'spotify' or source.service_id != conn.service_id or
            recording.organization_id != conn.organization_id or source.organization_id != conn.organization_id or
            recording.archived_at or recording.status == 'archived' or source.collector not in {'omkar', 'spotify-web-api'} or
            source.object_type not in {'track-details', 'spotify-track'} or not isinstance(source.payload_json, dict) or
            source.payload_json.get('id') != source.external_id or not source.sync_run_id or
            source.sync_run.sync_job.connection_id != conn.pk):
        return result
    for model, pk in ((type(conn), conn.pk), (type(source), source.pk),
                      (type(source.sync_run), source.sync_run_id),
                      (type(source.sync_run.sync_job), source.sync_run.sync_job_id)):
        if not active_rows(model.objects.filter(pk=pk)).exists():
            return result
    profiles = _profiles(conn)
    profile_id = source.sync_run.sync_job.cursor_json.get('profile_link_id')
    if profile_id:
        profiles = [(profile, value) for profile, value in profiles if str(profile.pk) == str(profile_id)]
    if not profiles or not _owned(conn, source, source.payload_json, profiles):
        return result
    if not active_rows(Recording.objects.filter(pk=recording.pk)).exists() or not active_rows(
            ArtistResourceGrant.objects.filter(recording=recording, artist=conn.artist)).exists():
        return result
    objects = PlatformObject.objects.filter(service=conn.service, object_type='track', external_id=source.external_id)
    obj = objects.first()
    if obj is None:
        return result
    identities = _identities(conn, obj, source.payload_json)
    matches = list(RecordingPlatformMatch.objects.filter(platform_object_id__in=identities))
    active = set(active_rows(RecordingPlatformMatch.objects.filter(pk__in=[m.pk for m in matches])).values_list('pk', flat=True))
    if (active_rows(PlatformObject.objects.filter(pk__in=identities)).count() != len(identities) or not matches or
            any(m.pk not in active or m.recording_id != recording.pk or m.review_status != 'accepted' or
                m.relationship_type != 'full_recording' for m in matches)):
        return result
    before = _snapshot(recording)
    result['before'] = before
    proposed = reported_credits(source.payload_json, {identity for _, identity in profiles}, conn.artist.name)
    if proposed is None and initial and not before:
        # Keep only the verified workspace attribution when the remaining
        # headline names/roles are malformed. A later complete receipt can
        # enrich this audited unknown performer instead of being blocked.
        identity = next(identity for _, identity in profiles
            if any(isinstance(a, dict) and a.get('id') == identity for a in source.payload_json.get('artists', [])))
        proposed = [{'external_id': identity, 'name': conn.artist.name, 'role': 'performer', 'billing_order': 1}]
    if proposed is None:
        result['status'] = 'unconfirmed'
        return result
    if _protected(recording, before):
        result['status'] = 'protected'
        return result
    previous = AuditEvent.objects.filter(organization=recording.organization, action='catalog.credits_sync',
        target__recording=recording).order_by('-created_at', '-pk').first()
    owned = previous is not None and previous.change_json.get('after') == before
    if before and not owned and not (repair_legacy and _legacy(recording, conn, before)):
        result['status'] = 'protected'
        return result
    # A headline-only response (including the official Web API alias) must not
    # erase explicit roles already established by a more detailed receipt.
    if before and any(row['role'] == 'performer' for row in proposed):
        result['status'] = 'unconfirmed'
        return result
    try:
        resolved = [{**row, 'artist_id': _artist_identity(conn, row['external_id'])} for row in proposed]
    except ValueError as exc:
        result.update(status='protected', reason=str(exc))
        return result
    # The connection's verified profile is the canonical workspace identity,
    # even where there is no ArtistPlatformMatch yet.
    for row in resolved:
        if row['external_id'] in {identity for _, identity in profiles}:
            row['artist_id'] = conn.artist_id
        row['artist_id'] = str(row['artist_id'])
    if len({row['artist_id'] for row in resolved}) != len(resolved):
        result.update(status='protected', reason='ambiguous_artist_identity')
        return result
    existing = {row['artist_id']: row for row in before}
    if any(row['artist_id'] not in {str(item['artist_id']) for item in resolved} for row in before):
        result['status'] = 'protected'
        return result
    after = []
    for row in resolved:
        old = existing.get(row['artist_id'])
        credit_id = old['id'] if old else str(uuid5(NAMESPACE_URL, f"bounced-artists:{recording.pk}:performer:{row['artist_id']}"))
        after.append({'id': credit_id, 'artist_id': row['artist_id'], 'role': row['role'],
                      'billing_order': row['billing_order'], 'credited_as': '', 'join_phrase': ''})
    after.sort(key=lambda row: row['id'])
    result.update(after=after, reported=proposed, identities=resolved, status='changed' if before != after else 'unchanged')
    return result


@transaction.atomic
def sync_recording_credits(recording, conn, source, *, repair_legacy=False, initial=False):
    Recording.objects.select_for_update().get(pk=recording.pk)
    # Portal and Operations editors lock child credit rows. Lock the same rows
    # before taking a snapshot so a concurrent edit cannot be overwritten.
    list(recording.credits.select_for_update().order_by('pk'))
    result = plan_recording_credits(recording, conn, source, repair_legacy=repair_legacy, initial=initial)
    if result['status'] != 'changed':
        return result
    existing = {row['id']: row for row in result['before']}
    for row in result['identities']:
        artist, _ = Artist.objects.get_or_create(pk=row['artist_id'], defaults={
            'organization': conn.organization, 'name': row['name'],
            'slug': 'spotify-' + UUID(row['artist_id']).hex, 'management_status': 'unmanaged'})
        if artist.organization_id != conn.organization_id or artist.archived_at:
            raise ValueError('artist_identity_conflict')
        platform, _ = PlatformObject.objects.get_or_create(service=conn.service, object_type='artist', external_id=row['external_id'],
            defaults={'title': row['name'], 'url': 'https://open.spotify.com/artist/' + row['external_id']})
        ArtistPlatformMatch.objects.get_or_create(artist=artist, platform_object=platform, defaults={'review_status': 'accepted'})
    for row in result['after']:
        old = existing.get(row['id'])
        if old != row:
            RecordingArtist.objects.update_or_create(pk=row['id'], defaults={'recording': recording,
                **{key: value for key, value in row.items() if key != 'id'}})
    after = _snapshot(recording)
    if after != result['after']:
        raise ValueError('credit_readback_mismatch')
    target = EntityTarget.objects.filter(organization=recording.organization, recording=recording).order_by('created_at', 'pk').first()
    target = target or EntityTarget.objects.create(organization=recording.organization, recording=recording)
    AuditEvent.objects.create(organization=recording.organization, action='catalog.credits_sync', target=target,
        target_label='Recording performer credits', change_json={'source_id': str(source.pk),
            'source_hash': source.payload_hash, 'before': result['before'], 'after': after, 'reported': result['reported']})
    return result
