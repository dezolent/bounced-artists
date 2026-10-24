import json

from django.core.management.base import BaseCommand
from django.db import transaction

from artists.models import Service
from catalog.models import RecordingPlatformMatch
from provenance.collectors.backends import catalog_enrichment_enabled, scraper_mode
from provenance.collectors.catalog_services import SEED_PLATFORMS, eligible_seed_profiles
from provenance.collectors.http import CollectionError
from provenance.collectors.providers import profile_locator
from provenance.collectors.runner import JOB_TYPE, eligible_profiles
from provenance.models import Connection, SyncJob


class Command(BaseCommand):
    help = 'Preview daily jobs for verified customer profiles and accepted catalog seeds; --apply writes configuration only.'

    def add_arguments(self, parser):
        parser.add_argument('--apply',action='store_true')

    @transaction.atomic
    def handle(self,*args,**options):
        links=[]
        identities=set()
        invalid=0
        for link in eligible_profiles().order_by('created_at','pk'):
            try:
                identity=(link.artist_id,link.platform_id,profile_locator(link.platform.slug,link.url))
                if identity in identities:
                    continue
                identities.add(identity)
                links.append((link.platform.slug,link,'profile_link_id'))
            except (CollectionError,ValueError):
                invalid+=1
        seeds=[]
        if catalog_enrichment_enabled():
            for profile in eligible_seed_profiles().order_by('created_at','pk'):
                try:
                    profile_locator(profile.platform.slug,profile.url)
                except (CollectionError,ValueError):
                    continue
                # Require catalog matches before creating a daily enrichment job.
                # Its runner revalidates exact source attribution, decisions and grants.
                if not RecordingPlatformMatch.objects.filter(platform_object__service=profile.platform,
                        recording__organization=profile.artist.organization,
                        recording__artist_grants__artist=profile.artist,recording__archived_at__isnull=True,
                        review_status='accepted',relationship_type='full_recording').exists():
                    continue
                for slug,source_slug in SEED_PLATFORMS.items():
                    if profile.platform.slug==source_slug:
                        seeds.append((slug,profile,'seed_profile_link_id'))
        wanted={(slug,str(link.pk),key) for slug,link,key in links+seeds}
        obsolete=[]
        for job in SyncJob.objects.filter(job_type=JOB_TYPE,enabled=True).select_related('connection__service'):
            key='seed_profile_link_id' if job.connection.service.slug in SEED_PLATFORMS else 'profile_link_id'
            if (job.connection.service.slug,str(job.cursor_json.get(key) if isinstance(job.cursor_json,dict) else None),key) not in wanted:
                obsolete.append(job.pk)
        if options['apply']:
            SyncJob.objects.filter(pk__in=obsolete).update(enabled=False)
            for slug,link,key in links+seeds:
                job=SyncJob.objects.filter(job_type=JOB_TYPE,connection__artist=link.artist,
                    connection__organization=link.artist.organization,connection__service__slug=slug,
                    **{f'cursor_json__{key}':str(link.pk)}).first()
                if job:
                    continue  # Preserve operator pauses and connection status.
                service,_=Service.objects.get_or_create(slug=slug,defaults={'name':{'shazam':'Shazam','musixmatch':'Musixmatch'}.get(slug,slug.title())})
                hosted=scraper_mode()=='hosted'
                conn=Connection.objects.create(organization=link.artist.organization,artist=link.artist,service=service,
                    auth_type='api_key' if hosted else 'public',secret_reference='env:OMKAR_API_KEY' if hosted else '',status='active')
                SyncJob.objects.create(connection=conn,job_type=JOB_TYPE,schedule='0 9 * * *',enabled=True,
                    cursor_json={key:str(link.pk)})
        self.stdout.write(json.dumps({'apply':options['apply'],'eligible_profiles':len(links),
            'catalog_enrichment_jobs':len(seeds),'invalid_profiles':invalid,'jobs_to_disable':len(obsolete)},sort_keys=True))
