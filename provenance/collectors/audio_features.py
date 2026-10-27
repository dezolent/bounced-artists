"""One-time official Spotify features for already authorized catalog tracks.

The API's returned analysis URL is metadata only and is never followed.
"""
import math
import re
from uuid import UUID

from provenance.models import SourceRecord
from .http import CollectionError, safe_error_code
from .providers import Collected, failure

ENDPOINT = 'spotify-audio-features'
UNIT_FIELDS = ('acousticness', 'danceability', 'energy', 'instrumentalness', 'liveness', 'speechiness', 'valence')


def validated_features(payload, external_id):
    if not re.fullmatch(r'[A-Za-z0-9]{22}', external_id) or not isinstance(payload, dict):
        raise CollectionError('invalid_audio_features')
    expected = {'id': external_id, 'uri': 'spotify:track:' + external_id,
        'track_href': 'https://api.spotify.com/v1/tracks/' + external_id,
        'analysis_url': 'https://api.spotify.com/v1/audio-analysis/' + external_id, 'type': 'audio_features'}
    if any(payload.get(key) != value for key, value in expected.items()):
        raise CollectionError('catalog_identity_mismatch')
    result = dict(expected)
    ranges = {**{field: (0, 1) for field in UNIT_FIELDS}, 'loudness': (-100, 10), 'tempo': (0, 1000)}
    for field, (minimum, maximum) in ranges.items():
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise CollectionError('invalid_audio_features')
        result[field] = value
    for field, minimum, maximum in [('key', -1, 11), ('mode', 0, 1), ('time_signature', 3, 7), ('duration_ms', 1, 2147483647)]:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise CollectionError('invalid_audio_features')
        result[field] = value
    return result


def feature_sources(organization_id, recording_id, external_id=None):
    from catalog.models import Recording
    from catalog.reconciliation import recording_identity_ids
    recording = Recording.objects.filter(organization_id=organization_id, pk=recording_id).first()
    if recording is None:
        return SourceRecord.objects.none()
    query = SourceRecord.objects.filter(organization_id=organization_id, service__slug='spotify',
        collector='spotify-web-api', collection_method='api', object_type=ENDPOINT,
        payload_json___catalog_seed__recording_id__in=recording_identity_ids(recording))
    if external_id is not None:
        query = query.filter(external_id=external_id)
    return query.order_by('-observed_at', '-created_at', '-id')


def has_features(organization_id, recording_id, external_id=None, *, artist=None):
    """Any saved feature on a currently accepted identity reserves this song."""
    from catalog.models import Recording
    recording = Recording.objects.filter(organization_id=organization_id, pk=recording_id).first()
    return recording is not None and latest_audio_features(recording, artist=artist) is not None


def collect_audio_features(fetch, seeds, *, seed_finished=None, access_state=None):
    access_state = {} if access_state is None else access_state
    for seed in seeds:
        try:
            UUID(str(seed['recording_id']))
            if seed.get('platform') != 'spotify' or not re.fullmatch(r'[A-Za-z0-9]{22}', seed.get('external_id', '')):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CollectionError('invalid_catalog_seed') from None
        tag = {key: str(seed[key]) for key in ('recording_id', 'platform', 'external_id')}
        if access_state.get('unavailable'):
            yield failure(ENDPOINT, tag['external_id'], CollectionError('spotify_audio_features_access_unavailable'))
            return
        try:
            body = validated_features(fetch('/audio-features/' + tag['external_id']), tag['external_id'])
            yield Collected(ENDPOINT, 'track', tag['external_id'], {**body, '_catalog_seed': tag})
        except CollectionError as exc:
            if safe_error_code(exc) == 'http_403':
                access_state['unavailable'] = True
                yield failure(ENDPOINT, tag['external_id'], CollectionError('spotify_audio_features_access_unavailable'))
                return  # App-level denial; do not repeat once per track.
            if safe_error_code(exc) in {'http_401', 'missing_spotify_credentials', 'invalid_spotify_token_response'}:
                raise
            yield failure(ENDPOINT, tag['external_id'], exc)
        if seed_finished is not None:
            seed_finished(seed)


def authorized_feature_source(source, recording, *, artist=None):
    """Apply fresh catalog/profile/grant/match authorization to private evidence."""
    from .catalog_services import catalog_seeds, enrichment_platform
    from catalog.reconciliation import recording_identity_ids
    if not source.sync_run_id or source.organization_id != recording.organization_id:
        return False
    job = source.sync_run.sync_job
    if enrichment_platform(job) != ENDPOINT or job.connection.service_id != source.service_id:
        return False
    if artist is not None and job.connection.artist_id != artist.pk:
        return False
    tag = source.payload_json.get('_catalog_seed')
    if not isinstance(tag, dict) or not isinstance(tag.get('recording_id'), str) or tag.get('recording_id') not in recording_identity_ids(recording) or tag.get('platform') != 'spotify' or tag.get('external_id') != source.external_id:
        return False
    return bool(catalog_seeds(job, recording_id=recording.pk, external_id=source.external_id,
        allow_paused=True, pending_only=False))


def latest_audio_features_for_recordings(recordings, *, artist=None):
    """Read private evidence in bulk; current scope is checked once per source job.

    Return a mapping keyed by the supplied canonical Recording UUIDs. Audited
    merge aliases locate immutable receipts without rewriting original payloads.
    """
    from catalog.reconciliation import recording_identity_map
    from portal.querysets import active_rows
    from .catalog_services import catalog_seeds, enrichment_platform
    recordings = list(recordings)
    if not recordings:
        return {}
    owners = {}
    identity_map = recording_identity_map(recordings)
    for recording in recordings:
        for identity in identity_map[str(recording.pk)]:
            owners.setdefault((recording.organization_id, identity), set()).add(recording.pk)
    sources = active_rows(SourceRecord.objects.filter(
        organization_id__in={recording.organization_id for recording in recordings}, service__slug='spotify',
        collector='spotify-web-api', collection_method='api', object_type=ENDPOINT,
        payload_json___catalog_seed__recording_id__in={identity for _, identity in owners}))
    if artist is not None:
        sources = sources.filter(sync_run__sync_job__connection__artist_id=artist.pk)
    sources = sources.select_related('sync_run__sync_job__connection__service').order_by('-observed_at', '-created_at', '-id')
    authorized, result = {}, {}
    for source in sources.iterator():
        if not source.sync_run_id:
            continue
        try:
            body = validated_features(source.payload_json, source.external_id)
        except CollectionError:
            continue
        tag = source.payload_json.get('_catalog_seed')
        if not isinstance(tag, dict) or not isinstance(tag.get('recording_id'), str) or tag.get('platform') != 'spotify' or tag.get('external_id') != source.external_id:
            continue
        candidates = owners.get((source.organization_id, tag.get('recording_id')), set())
        if len(candidates) != 1:
            continue
        canonical = next(iter(candidates))
        if canonical in result:
            continue
        job = source.sync_run.sync_job
        if enrichment_platform(job) != ENDPOINT or job.connection.service_id != source.service_id:
            continue
        if job.pk not in authorized:
            authorized[job.pk] = {(seed['recording_id'], seed['external_id']) for seed in
                catalog_seeds(job, allow_paused=True, pending_only=False)}
        if (str(canonical), source.external_id) in authorized[job.pk]:
            result[canonical] = body
    return result


def latest_audio_features(recording, *, artist=None):
    return latest_audio_features_for_recordings([recording], artist=artist).get(recording.pk)


def validate_feature_row(run, row):
    from .catalog_services import catalog_seeds, enrichment_platform
    if row.endpoint != ENDPOINT or row.kind != 'track' or row.incomplete or enrichment_platform(run.sync_job) != ENDPOINT:
        raise CollectionError('invalid_audio_features')
    body = validated_features(row.payload, row.external_id)
    tag = row.payload.get('_catalog_seed')
    if not isinstance(tag, dict) or tag.get('platform') != 'spotify' or tag.get('external_id') != row.external_id:
        raise CollectionError('invalid_catalog_seed')
    seeds = catalog_seeds(run.sync_job, recording_id=tag.get('recording_id'), external_id=row.external_id, pending_only=False)
    if not seeds or tag.get('recording_id') != seeds[0]['recording_id']:
        raise CollectionError('invalid_catalog_seed')
    row.payload = {**body, '_catalog_seed': {key: tag[key] for key in ('recording_id', 'platform', 'external_id')}}
    return not has_features(run.sync_job.connection.organization_id, tag['recording_id'], artist=run.sync_job.connection.artist)
