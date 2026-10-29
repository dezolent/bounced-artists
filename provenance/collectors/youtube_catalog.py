"""Attach videos using explicit, accepted catalog links in their descriptions."""
import re
from urllib.parse import urlsplit

from django.db import transaction
from django.db.models import Q

from artists.customer_workspaces import collection_artists
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.querysets import active_rows
from provenance.models import ReviewItem, SyncJob
from .providers import profile_locator
from .http import CollectionError
from .tiktok_catalog import _index, _target


def attach_youtube_match(conn, obj, source, row):
    if row.endpoint != 'video-details' or row.kind != 'video' or row.incomplete or not source.sync_run_id:
        return False
    payload = source.payload_json
    body = payload.get('video') if isinstance(payload, dict) else None
    if (not isinstance(body, dict) or str(body.get('id', '')) != row.external_id or
        source.external_id != row.external_id or source.service_id != conn.service_id or
        source.organization_id != conn.organization_id or source.object_type != row.endpoint):
        return False
    description = body.get('description')
    if not isinstance(description, str):
        return False
    entries = []
    for url in re.findall(r'https://[^\s<>"\']+', description):
        url = url.rstrip('.,;!?)')
        try:
            host = urlsplit(url).hostname
        except ValueError:
            return False
        platform = {'open.spotify.com': 'spotify', 'music.apple.com': 'apple-music',
                    'soundcloud.com': 'soundcloud', 'www.soundcloud.com': 'soundcloud'}.get(host)
        if platform:
            entries.append({'platform': platform, 'link': url})
    if not entries:
        return False
    return _attach_verified_match(conn, obj, source, row, body, entries)


@transaction.atomic
def _attach_verified_match(conn, obj, source, row, body, entries):
    job = active_rows(SyncJob.objects.filter(pk=source.sync_run.sync_job_id, enabled=True,
        job_type='daily_public_platform')).select_related('connection__artist', 'connection__service').first()
    if not job or job.connection_id != conn.pk or conn.status != 'active' or conn.service.slug != 'youtube' or not conn.artist_id or conn.organization_id != conn.artist.organization_id:
        return False
    if not active_rows(collection_artists()).filter(pk=conn.artist_id).exists() or not active_rows(type(conn).objects.filter(pk=conn.pk)).exists():
        return False
    from .runner import job_profile
    try:
        profile = job_profile(job)
        if not profile:
            return False
        profile_locator('youtube', profile.url)
    except (CollectionError, ValueError, TypeError):
        return False
    identities, _ = _index(conn.artist)
    targets = {_target({'streaming_links': [entry]}, identities) for entry in entries}
    if None in targets or len(targets) != 1:
        return False
    recording_id = next(iter(targets))
    recording = Recording.objects.filter(pk=recording_id).first()
    title = body.get('title')
    if not recording or not isinstance(title, str):
        return False
    from catalog.reconciliation import title_key
    # A description may promote a different song. Require the video title to
    # identify this recording as well as the explicit accepted streaming link.
    title = re.sub(r'\s*[\[(](?:official\s+)?(?:music\s+)?(?:video|audio|visualizer|lyric\s+video)[\])]\s*$', '', title, flags=re.I)
    candidates = {title_key(title)}
    if ' - ' in title:
        prefix, song = title.split(' - ', 1)
        if title_key(prefix) == title_key(conn.artist.name):
            candidates.add(title_key(song))
    if title_key(recording.title) not in candidates:
        return False
    obj = PlatformObject.objects.select_for_update().filter(pk=obj.pk, service=conn.service,
        object_type='video', external_id=row.external_id).first()
    if not obj or not active_rows(PlatformObject.objects.filter(pk=obj.pk)).exists():
        return False
    # Preserve all existing decisions, including aliases and rejected matches.
    from catalog.reconciliation import platform_identity_keys
    keys = platform_identity_keys(obj)
    aliases = [candidate.pk for candidate in PlatformObject.objects.filter(service__slug__in=('youtube', 'youtube-music'))
        .filter(Q(external_id=row.external_id) | Q(url__contains=row.external_id))
        .select_related('service') if keys & platform_identity_keys(candidate)]
    if (active_rows(PlatformObject.objects.filter(pk__in=aliases)).count() != len(aliases) or
        RecordingPlatformMatch.objects.filter(platform_object_id__in=aliases).exists() or
        ReviewItem.objects.filter(issue_type='platform_object_discovered', status__in=('resolved', 'dismissed'),
            source_record__organization=conn.organization, source_record__service=conn.service,
            source_record__external_id=row.external_id, candidates__target__artist=conn.artist).exists()):
        return False
    RecordingPlatformMatch.objects.create(recording_id=recording_id, platform_object=obj,
        relationship_type='full_recording', review_status='accepted',
        match_method='verified_streaming_link', confidence='1.000')
    return True
