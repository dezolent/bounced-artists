import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.db.models import Sum
from artists.models import Artist
from artists.imports.dezolent_bundle import Bundle
from catalog.models import Asset,AssetVersion
from publishing.models import Website
from portal.local_media import verified_source,install_media


class Command(BaseCommand):
    help='Verify/copy local export media into private development storage. Dry-run by default; never production.'

    def add_arguments(self,parser):
        parser.add_argument('--bundle',required=True)
        parser.add_argument('--source-root',required=True)
        parser.add_argument('--website',required=True)
        parser.add_argument('--artist',required=True)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--expected-sha256')
        parser.add_argument('--report',required=True)

    def handle(self,*args,**options):
        if settings.SETTINGS_MODULE!='config.settings.local' or settings.DATABASES['default']['HOST'] not in ('localhost','127.0.0.1','::1') or settings.STORAGES['default']['BACKEND']!='django.core.files.storage.FileSystemStorage':
            raise CommandError('This command requires local settings, loopback PostgreSQL and filesystem media storage.')
        copied=[]
        try:
            with transaction.atomic():
                bundle=Bundle(options['bundle'])
                artist=Artist.objects.select_for_update().get(pk=options['artist'],archived_at__isnull=True)
                website=Website.objects.select_for_update().get(domain=options['website'],artist=artist,organization=artist.organization)
                state=website.theme_config.get('dezolent_import',{})
                mapping=state.get('entity_ids',{})
                plan=[];receipt=[]
                for row in bundle.rows['assets']:
                    path=verified_source(options['source_root'],row)
                    asset=Asset.objects.select_for_update().get(pk=mapping[row['key']],organization=artist.organization,archived_at__isnull=True)
                    if asset.sha256 and asset.sha256!=row['sha256']:
                        raise ValidationError('An editorial asset checksum conflicts with the source; nothing was copied.')
                    if asset.current_version_id and asset.current_version.sha256!=row['sha256']:
                        raise ValidationError('An editorial upload conflicts with the source; nothing was copied.')
                    plan.append((asset,path,row))
                    receipt.append({'id':str(asset.pk),'sha256':row['sha256'],'bytes':row['bytes']})
                document={'artist':str(artist.pk),'website':str(website.pk),'manifest_sha256':bundle.manifest_hash,'files':receipt}
                checksum=hashlib.sha256(json.dumps(document,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                new_bytes=sum(row['bytes'] for asset,_,row in plan if not asset.current_version_id)
                used=AssetVersion.objects.filter(asset__artist_grants__artist=artist).aggregate(total=Sum('byte_size'))['total'] or 0
                if used+new_bytes>settings.PORTAL_ARTIST_STORAGE_QUOTA:
                    raise ValidationError('Local media would exceed the artist storage quota.')
                report=Path(options['report'])
                if report.suffix!='.json':
                    raise CommandError('Use a private JSON report path.')
                report.parent.mkdir(parents=True,exist_ok=True)
                report.write_text(json.dumps({**document,'sha256':checksum},indent=2)+'\n')
                if options['apply']:
                    if options['expected_sha256']!=checksum:
                        raise ValidationError('Apply requires the reviewed matching SHA-256; nothing was copied.')
                    for asset,path,row in plan:
                        if install_media(artist,asset,path,row):
                            copied.append(asset.current_version)
                self.stdout.write(json.dumps({'mode':'apply' if options['apply'] else 'dry-run','files':len(plan),'copies_needed':sum(not asset.current_version_id for asset,_,_ in plan) if not options['apply'] else len(copied),'copied':len(copied),'sha256':checksum}))
        except Exception as error:
            for version in copied:
                for field in (version.file,version.preview_file):
                    if field:
                        field.storage.delete(field.name)
            if isinstance(error,CommandError):
                raise
            if isinstance(error,(ValidationError,KeyError,ValueError,Artist.DoesNotExist,Website.DoesNotExist,Asset.DoesNotExist,OSError)):
                raise CommandError('Local media verification/import failed; no database changes applied. Check the source hashes, paths and mappings.') from None
            raise
