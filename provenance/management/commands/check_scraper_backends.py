"""Read-only config/readiness checks. No platform endpoint is scraped."""
import json
from django.core.management.base import BaseCommand, CommandError
from provenance.collectors.backends import SCRAPER_PLATFORMS, scraper_backend
from provenance.collectors.http import Budget, CollectionError, JsonClient, safe_error_code


class Command(BaseCommand):
    help='Validate six backend configurations; --probe checks only local self-hosted health endpoints.'

    def add_arguments(self,parser):
        parser.add_argument('--probe',action='store_true')

    def handle(self,*args,**options):
        result=[]
        failed=False
        budget=Budget(24)
        for platform in sorted(SCRAPER_PLATFORMS):
            row={'platform':platform}
            try:
                backend=scraper_backend(platform)
                row.update(backend=backend.mode,backend_identity=backend.identity,revision=backend.revision,status='configured')
                if options['probe']:
                    if backend.mode!='self_hosted':
                        raise CollectionError('invalid_scraper_backend')
                    response=JsonClient(backend.origin,{},budget).get('/health')
                    if response.get('status')!='ok':
                        raise CollectionError('invalid_response_shape')
                    row['status']='ready'
            except CollectionError as exc:
                row.update(status='failed',error_code=safe_error_code(exc));failed=True
            result.append(row)
        self.stdout.write(json.dumps({'probe':options['probe'],'backends':result},sort_keys=True))
        if failed:
            raise CommandError('Scraper backend checks failed; inspect fixed error codes above.')
