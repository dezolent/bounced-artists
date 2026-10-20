"""Verified local source files, not an arbitrary URL or browser upload importer."""
import hashlib
import uuid
from pathlib import Path,PurePosixPath
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.uploadedfile import UploadedFile
from catalog.models import AssetVersion
from .media import save_upload
from .models import EditorialChange


def verified_source(source_root,row):
    root=Path(source_root).resolve()
    relative=PurePosixPath(row['source_path'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0]!='public':
        raise ValidationError('Media source must be a relative path inside public/.')
    public=(root/'public').resolve()
    path=(root/str(relative)).resolve()
    if not public.is_relative_to(root) or not path.is_relative_to(public) or not path.is_file():
        raise ValidationError('Media source is missing or escapes public/.')
    if path.stat().st_size!=row['bytes'] or path.stat().st_size>250*1024*1024:
        raise ValidationError('Media file size does not match the verified export or exceeds the limit.')
    with path.open('rb') as handle:
        actual=hashlib.file_digest(handle,'sha256').hexdigest()
    if actual!=row['sha256']:
        raise ValidationError('Media file hash does not match the verified export.')
    return path


def install_media(artist,asset,path,row):
    if asset.organization_id!=artist.organization_id:
        raise ValidationError('Media asset must belong to this artist organization.')
    if asset.sha256 and asset.sha256!=row['sha256']:
        raise ValidationError('The asset checksum was edited; preserve it and reconcile before copying source media.')
    # Recheck immediately before copying; never replace an existing editorial upload.
    with path.open('rb') as handle:
        if hashlib.file_digest(handle,'sha256').hexdigest()!=row['sha256']:
            raise ValidationError('Source media changed after validation.')
    if asset.current_version_id:
        if asset.current_version.sha256!=row['sha256']:
            raise ValidationError('This asset has a different editorial file version; no replacement performed.')
        return False
    if row['mime_type'] in ('image/jpeg','image/png','image/webp'):
        with path.open('rb') as handle:
            upload=UploadedFile(file=handle,name=path.name,content_type=row['mime_type'],size=row['bytes'])
            save_upload(asset,upload,None,artist)
    else:
        # Trusted hash-verified source attachments (including SVG/JSON/fonts) are
        # private downloads only. They never get an inline or public preview.
        version=AssetVersion(asset=asset,original_name=path.name,byte_size=row['bytes'],mime_type=row['mime_type'],sha256=row['sha256'])
        suffix=path.suffix.lower() if path.suffix[1:].isalnum() and len(path.suffix)<12 else '.bin'
        try:
            with path.open('rb') as handle:
                version.file.save(f'{uuid.uuid4().hex}{suffix}',File(handle),save=False)
                version.save()
            asset.current_version=version
            asset.storage_key=version.file.name
            asset.rights_approved=False
            asset.save()
        except Exception:
            if version.file.name:
                version.file.storage.delete(version.file.name)
            raise
    if not asset.display_name:
        asset.display_name=path.name[:200]
        asset.save()
    EditorialChange.objects.create(organization=artist.organization,artist=artist,user=None,model_label='catalog.asset',object_id=str(asset.pk),action='import_media',changed_fields=['current_version'])
    return True
