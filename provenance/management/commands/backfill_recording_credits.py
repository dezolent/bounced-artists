"""Preview and hash-guard repair of collector-generated credits. No provider I/O."""
import hashlib
import json
from pathlib import Path
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from artists.customer_workspaces import collection_artists
from catalog.models import Recording, RecordingPlatformMatch
from portal.querysets import active_rows
from provenance.collectors.catalog_credits import plan_recording_credits, sync_recording_credits
from provenance.collectors.runner import LOCK_ID
from provenance.models import SourceRecord


class Command(BaseCommand):
    help = 'Preview saved Spotify performer-credit repair. No network. Apply requires the reviewed SHA-256.'

    def add_arguments(self, parser):
        parser.add_argument('--artist', type=uuid.UUID)
        parser.add_argument('--report', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expected-sha256', default='')

    def handle(self, *args, **options):
        if options['apply'] and not options['expected_sha256']:
            raise CommandError('--apply requires --expected-sha256 from a reviewed preview')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise CommandError('A platform collection/backfill is running; retry after it finishes.')
        try:
            with transaction.atomic():
                artists = active_rows(collection_artists())
                if options['artist']:
                    artists = artists.filter(pk=options['artist'])
                if options['artist'] and not artists.exists():
                    raise CommandError('Artist is not an active authorized collection workspace')
                results, pending = [], []
                for artist in artists.order_by('pk'):
                    recordings = active_rows(Recording.objects.filter(organization=artist.organization,
                        archived_at__isnull=True).exclude(status='archived'))
                    for recording in recordings.order_by('pk'):
                        if options['apply']:
                            Recording.objects.select_for_update().get(pk=recording.pk)
                        matches = active_rows(RecordingPlatformMatch.objects.filter(recording=recording,
                            review_status='accepted', relationship_type='full_recording', platform_object__service__slug='spotify'))
                        identities = matches.values('platform_object__external_id')
                        sources = active_rows(SourceRecord.objects.filter(organization=artist.organization,
                            service__slug='spotify', external_id__in=identities,
                            collector='omkar', object_type='track-details',
                            sync_run__sync_job__connection__artist=artist)).select_related(
                                'sync_run__sync_job__connection__artist', 'sync_run__sync_job__connection__service',
                                'sync_run__sync_job__connection__organization').order_by('-observed_at', '-created_at', '-pk')
                        source = sources.first()
                        if source is None:
                            continue
                        result = plan_recording_credits(recording, source.sync_run.sync_job.connection,
                            source, repair_legacy=True)
                        result['workspace_id'] = str(artist.pk)
                        results.append(result)
                        if result['status'] == 'changed':
                            pending.append((recording, source, result))
                summary = {'recordings_checked': len(results),
                    'recordings_changed': sum(r['status'] == 'changed' for r in results),
                    'protected': sum(r['status'] == 'protected' for r in results),
                    'unconfirmed': sum(r['status'] == 'unconfirmed' for r in results),
                    'unauthorized': sum(r['status'] == 'unauthorized' for r in results)}
                plan = {'version': 1, 'summary': summary, 'recordings': results}
                digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                if options['apply'] and digest != options['expected_sha256']:
                    raise CommandError('Credit plan changed or SHA-256 does not match; nothing was applied')
                if options['apply']:
                    for recording, source, expected in pending:
                        applied = sync_recording_credits(recording, source.sync_run.sync_job.connection,
                            source, repair_legacy=True)
                        applied['workspace_id'] = expected['workspace_id']
                        if applied != expected:
                            raise CommandError('Credit state changed during apply; nothing was applied')
            report = Path(options['report'])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({**plan, 'sha256': digest, 'applied': options['apply']}, indent=2) + '\n')
            report.chmod(0o600)
            self.stdout.write(json.dumps({**summary, 'sha256': digest, 'applied': options['apply'], 'report': str(report)}, sort_keys=True))
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
