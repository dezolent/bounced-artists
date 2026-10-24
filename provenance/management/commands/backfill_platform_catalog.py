"""Replay stored track evidence without provider requests or metric duplication."""
import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from catalog.models import PlatformObject
from provenance.collectors.catalog import ENDPOINTS, ensure_catalog_recording
from provenance.collectors.providers import Collected
from provenance.collectors.runner import JOB_TYPE, LOCK_ID, eligible_profiles
from provenance.models import SourceRecord, SyncJob
from portal.querysets import active_rows


class Command(BaseCommand):
    help = 'Preview catalog discovery/metadata backfill from saved verified customer track receipts. No network. --apply commits.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--artist', type=uuid.UUID)
        parser.add_argument('--limit', type=int, default=2000)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 100000:
            raise CommandError('limit must be between 1 and 100000')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)',[LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise CommandError('A platform collection/backfill is running; retry after it finishes.')
        summary = {'apply':options['apply'],'receipts_processed':0,'recordings_created':0,
                   'fields_filled':0,'covers_added':0,'identifiers_added':0,'genres_added':0}
        try:
            with transaction.atomic():
                profiles = eligible_profiles()
                if options['artist']:
                    profiles = profiles.filter(artist_id=options['artist'])
                allowed = {str(pk) for pk in profiles.values_list('pk',flat=True)}
                jobs = active_rows(SyncJob.objects.filter(job_type=JOB_TYPE,enabled=True,
                    connection__status='active',connection__service__slug__in=ENDPOINTS)).select_related(
                        'connection__service','connection__artist','connection__organization').order_by('pk')
                for job in jobs:
                    if str(job.cursor_json.get('profile_link_id','')) not in allowed or not active_rows(type(job.connection).objects.filter(pk=job.connection_id)).exists():
                        continue
                    sources = SourceRecord.objects.filter(sync_run__sync_job=job,
                        service=job.connection.service,organization=job.connection.organization,
                        collector__in=('omkar','spotify-web-api'),object_type__in=ENDPOINTS[job.connection.service.slug])
                    newest = sources.order_by('object_type','external_id','-observed_at','-created_at','-pk').distinct('object_type','external_id').values('pk')
                    for source in sources.filter(pk__in=newest).select_related('sync_run__sync_job__connection').order_by('-observed_at','-pk').iterator(chunk_size=100):
                        if summary['receipts_processed'] >= options['limit']:
                            break
                        obj = active_rows(PlatformObject.objects.filter(service=job.connection.service,
                            object_type='track',external_id=source.external_id)).first()
                        if obj is None:
                            continue
                        row = Collected(source.object_type,'track',source.external_id,source.payload_json)
                        summary['recordings_created'] += ensure_catalog_recording(job.connection,obj,source,row,metadata_counts=summary)
                        summary['receipts_processed'] += 1
                    if summary['receipts_processed'] >= options['limit']:
                        break
                if not options['apply']:
                    transaction.set_rollback(True)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)',[LOCK_ID])
        self.stdout.write(json.dumps(summary,sort_keys=True))
