"""Reviewable metadata backfill and retirement of artist-owned brand references."""
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand,CommandError
from django.db import transaction

from artists.models import ArtistResourceGrant
from catalog.models import Asset
from catalog.brand_library import legacy_brand_query
from portal.asset_display import suggested_category
from portal.models import EditorialChange


class Command(BaseCommand):
    help='Infer blank asset categories and remove known shared-logo grants; originals/evidence remain unchanged.'

    def add_arguments(self,parser):
        parser.add_argument('--report',required=True)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--expected-sha256')

    @transaction.atomic
    def handle(self,*args,**options):
        assets=Asset.objects.select_for_update(of=('self',)).filter(archived_at__isnull=True).order_by('pk')
        shared_ids=set(assets.filter(legacy_brand_query()).values_list('pk',flat=True))
        categories=[]
        for asset in assets.filter(category='').exclude(pk__in=shared_ids).select_related('current_version'):
            category=suggested_category(asset)
            if category:
                categories.append({'asset':str(asset.pk),'category':category,'revision':asset.revision})
        grants=list(ArtistResourceGrant.objects.select_for_update().filter(asset_id__in=shared_ids).order_by('pk'))
        report={'categories':categories,'shared_grants':[{'id':str(row.pk),'asset':str(row.asset_id),'artist':str(row.artist_id),'capability':row.capability} for row in grants]}
        digest=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path=Path(options['report'])
        if path.suffix!='.json':
            raise CommandError('Use a private JSON report path, normally under artifacts/.')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({**report,'sha256':digest},indent=2)+'\n')
        if options['apply']:
            if options['expected_sha256']!=digest:
                raise CommandError('The reviewed SHA-256 must match; no changes applied.')
            for row in categories:
                asset=assets.get(pk=row['asset'])
                asset.category=row['category']
                asset.revision+=1
                asset.save(update_fields=['category','revision','updated_at'])
                for artist in {grant.artist for grant in asset.artist_grants.select_related('artist')}:
                    EditorialChange.objects.create(organization=artist.organization,artist=artist,user=None,model_label='catalog.asset',object_id=str(asset.pk),action='categorize',changed_fields=['category'])
            for grant in grants:
                EditorialChange.objects.create(organization=grant.artist.organization,artist=grant.artist,user=None,model_label='catalog.asset',object_id=str(grant.asset_id),action='shared_library',changed_fields=['resource_grants'])
                grant.delete()
        self.stdout.write(json.dumps({'mode':'apply' if options['apply'] else 'dry-run','categories':len(categories),'shared_grants':len(grants),'sha256':digest}))
