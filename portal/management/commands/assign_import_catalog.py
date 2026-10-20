"""Explicit, reviewable bootstrap from persisted UUID mappings, never from credits."""
import hashlib
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from artists.models import Artist, ArtistResourceGrant
from catalog.models import Recording, Release, Asset
from rights.models import MusicalWork, Party
from publishing.models import Website
from portal.models import EditorialChange


class Command(BaseCommand):
    help='Dry-run explicit catalog grants from a website import mapping; --apply requires the reviewed hash.'

    def add_arguments(self,parser):
        parser.add_argument('--website',required=True)
        parser.add_argument('--artist',required=True)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--expected-sha256')
        parser.add_argument('--report',required=True,help='Private local JSON report path, normally under artifacts/.')

    @transaction.atomic
    def handle(self,*args,**options):
        try:
            artist=Artist.objects.select_for_update().get(pk=options['artist'],archived_at__isnull=True)
            site=Website.objects.select_for_update().get(domain=options['website'],artist=artist,organization=artist.organization)
        except (Artist.DoesNotExist,Website.DoesNotExist,ValueError):
            raise CommandError('Select an existing artist and its own website.') from None
        mapping=site.theme_config.get('dezolent_import',{}).get('entity_ids',{})
        if not mapping:
            raise CommandError('This website has no persisted supported import mappings.')
        rows=[]
        for kind,model in [('recording',Recording),('release',Release),('asset',Asset),('work',MusicalWork),('party',Party)]:
            prefixes=(kind+':','remote_asset:') if kind=='asset' else (kind+':',)
            ids={value for key,value in mapping.items() if key.startswith(prefixes)}
            found={str(value) for value in model.objects.filter(pk__in=ids,organization=artist.organization,archived_at__isnull=True).values_list('pk',flat=True)}
            if ids-found:
                raise CommandError('An import mapping is stale, archived or belongs to another organization; no grants applied.')
            rows.extend({'kind':kind,'id':value} for value in sorted(found))
        report={'artist':str(artist.pk),'website':str(site.pk),'grants':rows}
        digest=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path=Path(options['report'])
        if path.suffix!='.json':
            raise CommandError('Use a JSON report path.')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({**report,'sha256':digest},indent=2)+'\n')
        created=0
        if options['apply']:
            if options['expected_sha256']!=digest:
                raise CommandError('The reviewed SHA-256 is required and must match; no grants applied.')
            for row in rows:
                _,new=ArtistResourceGrant.objects.get_or_create(artist=artist,**{row['kind']+'_id':row['id']},defaults={'capability':'manage'})
                created+=int(new)
            EditorialChange.objects.create(organization=artist.organization,artist=artist,user=None,model_label='artists.artist',object_id=str(artist.pk),action='assign_import',changed_fields=['resource_grants'])
        counts={kind:sum(row['kind']==kind for row in rows) for kind in ('recording','release','asset','work','party')}
        self.stdout.write(json.dumps({'mode':'apply' if options['apply'] else 'dry-run','counts':counts,'created':created,'sha256':digest}))
