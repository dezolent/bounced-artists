"""Source-verified asset transfer. Export locally; dry-run/review hash before apply.

Run from the repository/application root with its existing settings environment.
Never transfers credentials, permissions, visibility, rights approvals or DB dumps.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, unquote

sys.path.insert(0,str(Path.cwd()))


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def metadata(asset,artist):
    from catalog.models import RecordingAsset, ReleaseAsset
    from publishing.models import SEORecord
    from portal.asset_display import suggested_category
    from catalog.brand_library import BRANDS,legacy_brand_paths
    name=PurePosixPath(asset.source_path or urlsplit(asset.source_url).path).name
    if not name and asset.current_version_id:
        name=asset.current_version.original_name
    name=unquote(name)
    if not name or len(name)>200 or '/' in name or '\\' in name:
        raise ValueError('A safe source filename is required.')
    category='website_asset' if name=='.gitkeep' else asset.category or suggested_category(asset)
    lower=name.lower()
    if asset.source_path in legacy_brand_paths():
        category=category or 'website_asset'
        brand=BRANDS.get(PurePosixPath(name).stem.replace('titkok','tiktok'),PurePosixPath(name).stem)
        purpose=f'{brand} logo for website links'
    else:
        if not category:
            category='banner_image' if 'hero' in lower or not asset.source_path else 'website_asset'
        titles=list(RecordingAsset.objects.filter(asset=asset).order_by('recording__title').values_list('recording__title',flat=True).distinct())
        if not titles:
            titles=list(ReleaseAsset.objects.filter(asset=asset).order_by('release__title').values_list('release__title',flat=True).distinct())
        if not titles:
            titles=list(SEORecord.objects.filter(og_asset=asset).order_by('page__title').values_list('page__title',flat=True).distinct())
        subject=' / '.join(titles[:3]) or PurePosixPath(name).stem.replace('-',' ').replace('_',' ')
        role={'cover_art':'Cover artwork','lyrics_text':'Lyrics text','lyrics_srt':'Timed lyrics','artist_logo':'Artist logo','artist_icon':'Website icon','banner_image':'Website banner','profile_image':'Artist profile image','website_asset':'Website supporting file','audio':'Audio file','video':'Video file','document':'Document'}[category]
        purpose=f'{role}: {subject}'
    return {'mime_type':asset.mime_type or mimetypes.guess_type(name)[0] or 'application/octet-stream','display_name':name,'category':category,'alt_text':f'{artist.name} — {purpose}'[:500],
            'description':f'{purpose}. {name} is a {asset.mime_type or "source"} asset associated with {artist.name}’s website catalog.'}


def export_bundle(root,website,artist_slug):
    from django.conf import settings
    from catalog.models import Asset
    from publishing.models import Website
    if settings.SETTINGS_MODULE!='config.settings.local':
        raise ValueError('Export requires separate local development settings.')
    root=Path(root)
    if (root/'manifest.json').exists():raise ValueError('Choose a new export directory.')
    site=Website.objects.select_related('artist__organization').get(domain=website,artist__slug=artist_slug)
    mapping=site.theme_config['dezolent_import']['entity_ids']
    ids={value for key,value in mapping.items() if key.startswith(('asset:','remote_asset:'))}
    assets=list(Asset.objects.filter(pk__in=ids,organization=site.organization,archived_at__isnull=True).select_related('current_version').order_by('pk'))
    if len(assets)!=len(ids):raise ValueError('Source mappings must resolve to active, owned assets.')
    (root/'files').mkdir(parents=True,exist_ok=True)
    rows=[]
    for asset in assets:
        row={'id':str(asset.pk),'source_repository':asset.source_repository,'source_path':asset.source_path,'source_url':asset.source_url,
             'metadata':{key:(getattr(asset,key) or value) if key in {'alt_text','description'} else value for key,value in metadata(asset,site.artist).items()},'file':None}
        version=asset.current_version
        if version:
            if not version.original_name or version.original_name in {'.','..'} or '/' in version.original_name or '\\' in version.original_name:
                raise ValueError('Unsafe original filename.')
            with version.file.open('rb') as handle:data=handle.read(250*1024*1024+1)
            if len(data)!=version.byte_size or hashlib.sha256(data).hexdigest()!=version.sha256:
                raise ValueError('Stored development file differs from its immutable version.')
            directory=root/'files'/version.sha256
            directory.mkdir(exist_ok=True)
            (directory/version.original_name).write_bytes(data)
            mime=version.mime_type
            if version.original_name=='api-catalog':
                json.loads(data.decode('utf-8'))
                mime='application/json'
            row['file']={'sha256':version.sha256,'bytes':version.byte_size,'mime_type':mime,'original_name':version.original_name}
        rows.append(row)
    body={'format':1,'website':site.domain,'artist_slug':site.artist.slug,'organization_slug':site.organization.slug,'assets':rows}
    body['sha256']=digest(body)
    (root/'manifest.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
    return {'assets':len(rows),'files':sum(bool(r['file']) for r in rows),'bytes':sum(r['file']['bytes'] for r in rows if r['file']),'sha256':body['sha256']}


def read_bundle(root):
    from django.core.management.base import CommandError
    root=Path(root).resolve()
    try:
        body=json.loads((root/'manifest.json').read_text())
        checksum=body.pop('sha256')
        if body.get('format')!=1 or digest(body)!=checksum:raise ValueError
        ids=set()
        for row in body['assets']:
            if row['id'] in ids:raise ValueError
            ids.add(row['id'])
            if set(row['metadata'])!={'display_name','category','alt_text','description','mime_type'}:raise ValueError
            if not all(isinstance(value,str) and value.strip() for value in row['metadata'].values()):raise ValueError
            file=row['file']
            if file:
                sha=file['sha256']
                if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError
                name=file['original_name']
                if not isinstance(name,str) or not name or name in {'.','..'} or '/' in name or '\\' in name or len(name)>255:raise ValueError
                path=(root/'files'/sha/name).resolve()
                if not path.is_relative_to(root/'files') or path.stat().st_size!=file['bytes'] or not 0<=file['bytes']<=250*1024*1024:raise ValueError
                with path.open('rb') as handle:
                    if hashlib.file_digest(handle,'sha256').hexdigest()!=sha:raise ValueError
        body['sha256']=checksum
        return body
    except (ValueError,KeyError,TypeError,OSError):
        raise CommandError('Bundle metadata/file validation failed; no changes applied.') from None


def sync_bundle(root,report_path,*,apply=False,expected_sha256=None):
    from django.conf import settings
    from django.core.management.base import CommandError
    from django.db import transaction,connection
    from catalog.models import Asset
    from publishing.models import Website
    from portal.local_media import install_media
    from portal.models import EditorialChange
    body=read_bundle(root)
    created=[]
    try:
        with transaction.atomic():
            if connection.vendor!='postgresql':raise CommandError('PostgreSQL is required.')
            with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_xact_lock(74231, hashtext(%s))',['asset-file-sync'])
            site=Website.objects.select_for_update().select_related('artist__organization').get(domain=body['website'],artist__slug=body['artist_slug'],organization__slug=body['organization_slug'])
            allowed={value for key,value in site.theme_config['dezolent_import']['entity_ids'].items() if key.startswith(('asset:','remote_asset:'))}
            plan=[];targets=[]
            for row in body['assets']:
                if row['id'] not in allowed:raise CommandError('Asset lacks the target website source mapping.')
                asset=Asset.objects.select_for_update(of=('self',)).select_related('current_version').get(pk=row['id'],organization=site.organization,archived_at__isnull=True)
                if any(getattr(asset,key)!=row[key] for key in ('source_repository','source_path','source_url')):raise CommandError('Asset source identity changed; reconcile before syncing.')
                before={key:getattr(asset,key) for key in row['metadata']}
                changes={key:value for key,value in row['metadata'].items() if not before[key] or key=='display_name' and before[key]=='Catalog asset'}
                snapshot={**before,'revision':asset.revision,'current_version':str(asset.current_version_id) if asset.current_version_id else None,'visibility':asset.visibility,'rights_approved':asset.rights_approved,'storage_key':asset.storage_key}
                plan.append({'id':row['id'],'before':snapshot,'metadata':changes,'copy':bool(row['file'] and not asset.current_version_id)})
                for key,value in changes.items():setattr(asset,key,value)
                asset.full_clean()
                targets.append((asset,row,changes))
            document={'bundle_sha256':body['sha256'],'changes':plan}
            checksum=digest(document)
            path=Path(report_path);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({**document,'sha256':checksum},indent=2,ensure_ascii=False)+'\n')
            summary={'mode':'apply' if apply else 'dry-run','assets':len(plan),'metadata_updates':sum(bool(row['metadata']) for row in plan),'files_needed':sum(row['copy'] for row in plan),'files_copied':0,'preserved_uploads':sum(bool(row['before']['current_version']) for row in plan),'sha256':checksum}
            if apply:
                from django.db.models import Sum
                from catalog.models import AssetVersion
                used=AssetVersion.objects.filter(asset__artist_grants__artist=site.artist).aggregate(total=Sum('byte_size'))['total'] or 0
                needed=sum(row['file']['bytes'] for asset,row,changes in targets if row['file'] and not asset.current_version_id)
                if used+needed>settings.PORTAL_ARTIST_STORAGE_QUOTA:
                    raise CommandError('Asset sync exceeds the configured artist storage quota.')
                if checksum!=expected_sha256:raise CommandError('Reviewed plan SHA-256 does not match; no changes applied.')
                if settings.SETTINGS_MODULE=='config.settings.production' and (not settings.PORTAL_UPLOADS_ENABLED or settings.STORAGES['default']['BACKEND']!='storages.backends.s3.S3Storage'):
                    raise CommandError('Production sync requires the configured private R2 storage.')
                for asset,row,changes in targets:
                    copied=False
                    approved=asset.rights_approved
                    if row['file'] and not asset.current_version_id:
                        try:
                            copied=install_media(site.artist,asset,Path(root)/'files'/row['file']['sha256']/row['file']['original_name'],row['file'])
                        except Exception:
                            if asset.current_version_id:
                                created.append(asset.current_version)
                            raise
                        if copied:
                            created.append(asset.current_version)
                            asset.rights_approved=approved
                    if changes or copied:
                        asset.revision+=1
                        asset.save()
                        EditorialChange.objects.create(organization=site.organization,artist=site.artist,user=None,model_label='catalog.asset',object_id=str(asset.pk),action='sync_assets',changed_fields=list(changes)+(['current_version'] if copied else []))
                    if copied:
                        with asset.current_version.file.open('rb') as stream:
                            if hashlib.file_digest(stream,'sha256').hexdigest()!=row['file']['sha256']:raise CommandError('Stored original read-back failed.')
                        if asset.current_version.preview_file:
                            with asset.current_version.preview_file.open('rb') as stream:
                                from PIL import Image
                                with Image.open(stream) as image:image.verify()
                    asset.refresh_from_db()
                    if any(getattr(asset,key)!=value for key,value in changes.items()):raise CommandError('Metadata read-back failed.')
                summary['files_copied']=len(created)
            return summary
    except Exception as error:
        for version in created:
            for field in (version.file,version.preview_file):
                if field:field.storage.delete(field.name)
        if isinstance(error,(Asset.DoesNotExist,Website.DoesNotExist)):
            raise CommandError('Target asset/website ownership does not match; no changes applied.') from None
        raise


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.local')
    import django
    django.setup()
    from django.core.management.base import CommandError
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    export=sub.add_parser('export');export.add_argument('--output',required=True);export.add_argument('--website',required=True);export.add_argument('--artist',required=True)
    sync=sub.add_parser('sync');sync.add_argument('--bundle',required=True);sync.add_argument('--report',required=True);sync.add_argument('--apply',action='store_true');sync.add_argument('--expected-sha256')
    args=parser.parse_args()
    try:
        result=export_bundle(args.output,args.website,args.artist) if args.action=='export' else sync_bundle(args.bundle,args.report,apply=args.apply,expected_sha256=args.expected_sha256)
    except (CommandError,ValueError):
        parser.exit(1,'Asset sync validation failed; inspect the private plan and source identities. No partial database changes were committed.\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
