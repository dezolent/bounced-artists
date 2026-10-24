"""Private lyric candidates backed by their immutable collection receipt.

No lyric file is downloaded, written to ephemeral disk, approved or published.
An existing lyric association (including a removed one) reserves the decision.
"""
from hashlib import sha256
from uuid import UUID

from django.db import transaction

from artists.customer_workspaces import collection_artists
from artists.models import ArtistResourceGrant
from catalog.models import Asset, PlatformObject, Recording, RecordingAsset
from portal.querysets import active_rows
from provenance.models import Connection, SourceRecord

LYRIC_LIMIT = 100 * 1024
SOURCE_REPOSITORY = 'source-record'
LYRIC_ENDPOINT = 'musixmatch-track-lyrics'


def lyric_text(payload):
    """Accept only the explicit unrestricted full-text response contract."""
    if not isinstance(payload, dict) or payload.get('has_lyrics') is not True:
        return None
    lyrics = payload.get('lyrics')
    if not isinstance(lyrics, dict) or lyrics.get('is_restricted') is not False:
        return None
    value = lyrics.get('text')
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        return None
    try:
        if len(value.encode('utf-8')) > LYRIC_LIMIT:
            return None
    except UnicodeEncodeError:
        return None
    return value


def _target(conn, obj, source, *, allow_paused=False):
    """Revalidate ownership and the current stable match, never title/ISRC."""
    if (conn is None or conn.status != 'active' or conn.service.slug != 'musixmatch' or
            not conn.artist_id or conn.artist.organization_id != conn.organization_id or
            source.organization_id != conn.organization_id or source.service_id != conn.service_id or
            source.collector != 'omkar' or source.object_type != LYRIC_ENDPOINT or
            obj.service_id != conn.service_id or obj.object_type != 'track' or
            obj.external_id != source.external_id or not source.sync_run_id or
            source.sync_run.sync_job.connection_id != conn.pk):
        return None
    if not active_rows(collection_artists()).filter(pk=conn.artist_id).exists():
        return None
    if not active_rows(PlatformObject.objects.filter(pk=obj.pk)).exists():
        return None
    from .catalog_services import validated_recording
    return validated_recording(conn, obj, source, allow_paused=allow_paused)


@transaction.atomic
def import_lyrics(conn, obj, source, row):
    """Return whether a new private lyric candidate and artist grant were added."""
    if row.endpoint != LYRIC_ENDPOINT or row.kind != 'track' or row.incomplete:
        return False
    conn = active_rows(Connection.objects.filter(pk=conn.pk)).select_related('artist', 'service').first()
    source = active_rows(SourceRecord.objects.filter(pk=source.pk)).select_related('sync_run__sync_job').first()
    obj = PlatformObject.objects.select_for_update().filter(pk=obj.pk).first()
    if source is None or obj is None or row.external_id != source.external_id:
        return False
    text = lyric_text(source.payload_json)
    if text is None or source.private_asset_id:
        return False
    recording = _target(conn, obj, source)
    if recording is None:
        return False
    Recording.objects.select_for_update().get(pk=recording.pk)
    # Include inactive/archived assets and relations deliberately: never import
    # around an artist's existing or removed lyric choice.
    if (RecordingAsset.objects.filter(recording=recording, purpose='lyrics').exists() or
            RecordingAsset.objects.filter(recording=recording,
                asset__category__in=[Asset.Category.LYRICS_TEXT, Asset.Category.LYRICS_SRT]).exists()):
        return False
    asset = Asset.objects.create(organization_id=recording.organization_id,
        category=Asset.Category.LYRICS_TEXT, source_repository=SOURCE_REPOSITORY,
        source_path=str(source.pk), display_name=f'{recording.title[:170]} lyrics candidate',
        mime_type='text/plain', sha256=sha256(text.encode('utf-8')).hexdigest(),
        visibility='private', rights_approved=False, rights_basis='Musixmatch-reported; rights unverified',
        description=f'Private Musixmatch lyric candidate. Source record: {source.pk}. Review before use.')
    ArtistResourceGrant.objects.create(artist_id=conn.artist_id, asset=asset, capability='manage')
    RecordingAsset.objects.create(recording=recording, asset=asset, purpose='lyrics', is_primary=True)
    source.private_asset = asset
    source.save(update_fields=['private_asset', 'updated_at'])
    return True


def source_lyric_preview(asset):
    """Read one exact receipt only when it still backs this private lyric asset."""
    if (asset.source_repository != SOURCE_REPOSITORY or asset.category != Asset.Category.LYRICS_TEXT or
            asset.visibility != 'private' or asset.current_version_id or asset.archived_at):
        return None
    try:
        source_id = UUID(asset.source_path)
    except (ValueError, TypeError, AttributeError):
        return None
    source = active_rows(SourceRecord.objects.filter(pk=source_id,
        organization_id=asset.organization_id, private_asset_id=asset.pk)).select_related('sync_run__sync_job').first()
    if source is None or not source.sync_run_id:
        return None
    conn = active_rows(Connection.objects.filter(pk=source.sync_run.sync_job.connection_id)).select_related('artist', 'service').first()
    if conn is None:
        return None
    obj = PlatformObject.objects.filter(service_id=source.service_id, object_type='track', external_id=source.external_id).first()
    if obj is None:
        return None
    recording = _target(conn, obj, source, allow_paused=True)
    if recording is None or not active_rows(Asset.objects.filter(pk=asset.pk)).exists():
        return None
    if not active_rows(RecordingAsset.objects.filter(recording=recording, asset=asset, purpose='lyrics')).exists():
        return None
    if not active_rows(ArtistResourceGrant.objects.filter(artist_id=conn.artist_id, asset=asset)).exists():
        return None
    text = lyric_text(source.payload_json)
    if text is None or sha256(text.encode('utf-8')).hexdigest() != asset.sha256:
        return None
    return text
