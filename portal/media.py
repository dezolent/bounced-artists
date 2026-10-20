import hashlib
import io
import re
import uuid
import warnings
from pathlib import PurePath
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from catalog.models import AssetVersion

MiB = 1024 * 1024


def prepare_upload(upload):
    """Validate bytes, not browser MIME declarations; never render arbitrary files inline."""
    if not settings.PORTAL_UPLOADS_ENABLED:
        raise ValidationError('Durable file storage is not configured. Contact your operator to enable uploads.')
    if upload.size > 250 * MiB or upload.size <= 0:
        raise ValidationError('The file is empty or exceeds the 250 MiB upload limit.')
    header = upload.read(64)
    upload.seek(0)
    name = PurePath(upload.name.replace('\\', '/')).name[:255]
    suffix = PurePath(name).suffix.lower()
    preview = None
    width = height = None
    if header.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')) or (header[:4] == b'RIFF' and header[8:12] == b'WEBP'):
        if upload.size > 20 * MiB:
            raise ValidationError('Artwork must be 20 MiB or smaller.')
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    width, height = image.size
                    if width * height > 40_000_000:
                        raise ValidationError('Artwork must contain at most 40 megapixels.')
                    image.load()
                    mime = {'JPEG':'image/jpeg', 'PNG':'image/png', 'WEBP':'image/webp'}.get(image.format)
                    if not mime:
                        raise ValidationError('Use JPEG, PNG or WebP artwork.')
                    raster = ImageOps.exif_transpose(image).convert('RGB')
                    raster.thumbnail((2400,2400))
                    clean = Image.new('RGB', raster.size)
                    clean.paste(raster)
                    output = io.BytesIO()
                    clean.save(output, format='WEBP', quality=88)
                    preview = ContentFile(output.getvalue(), name=f'{uuid.uuid4().hex}.webp')
                    preview.image_width,preview.image_height=clean.size
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValidationError('This artwork could not be safely decoded.')
        extension = {'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[mime]
    elif header[:4] == b'RIFF' and header[8:12] == b'WAVE':
        mime, extension = 'audio/wav', '.wav'
    elif header.startswith(b'fLaC'):
        mime, extension = 'audio/flac', '.flac'
    elif header.startswith(b'ID3') or (header[:1] == b'\xff' and len(header)>1 and header[1] & 0xE0 == 0xE0):
        mime, extension = 'audio/mpeg', '.mp3'
    elif header[4:8] == b'ftyp' and header[8:12] in (b'M4A ',b'M4B ',b'isom',b'mp42'):
        mime, extension = 'audio/mp4', '.m4a'
    elif header.startswith(b'%PDF-'):
        mime, extension = 'application/pdf', '.pdf'
    elif suffix in ('.txt','.csv'):
        if upload.size > 25 * MiB:
            raise ValidationError('Text attachments must be 25 MiB or smaller.')
        try:
            value = upload.read().decode('utf-8')
            if '\x00' in value:
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            raise ValidationError('Text attachments must be valid UTF-8 without binary data.')
        mime, extension = ('text/csv','.csv') if suffix == '.csv' else ('text/plain','.txt')
    else:
        raise ValidationError('Supported files: JPEG, PNG, WebP, WAV, FLAC, MP3, M4A, PDF, TXT and CSV.')
    if not mime.startswith('audio/') and not mime.startswith('image/') and upload.size > 25 * MiB:
        raise ValidationError('Attachments must be 25 MiB or smaller.')
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return {'original_name':name, 'byte_size':upload.size, 'mime_type':mime, 'sha256':digest.hexdigest(), 'width':width, 'height':height}, f'{uuid.uuid4().hex}{extension}', preview


def save_upload(asset, upload, user, artist):
    metadata, filename, preview = prepare_upload(upload)
    used = AssetVersion.objects.filter(asset__artist_grants__artist=artist).aggregate(size=Sum('byte_size'))['size'] or 0
    if used + upload.size > settings.PORTAL_ARTIST_STORAGE_QUOTA:
        raise ValidationError('This artist has reached their storage quota. Contact your operator.')
    version = AssetVersion(asset=asset, creator=user, **metadata)
    if preview:
        version.preview_width,version.preview_height=preview.image_width,preview.image_height
    stored = []
    try:
        version.file.save(filename, upload, save=False)
        stored.append(version.file)
        if preview:
            version.preview_file.save(preview.name, preview, save=False)
            stored.append(version.preview_file)
        version.save()
        asset.current_version = version
        asset.storage_key = version.file.name
        for key in ('mime_type','sha256','width','height'):
            setattr(asset,key,metadata[key])
        asset.rights_approved = False
        asset.save()
    except Exception:
        for field in stored:
            field.storage.delete(field.name)
        raise
    return version


def file_response(request, version, *, preview=False):
    field = version.preview_file if preview else version.file
    if not field:
        from django.http import Http404
        raise Http404('No uploaded preview is available.')
    try:
        stream = field.open('rb')
    except (FileNotFoundError, OSError):
        from django.http import Http404
        raise Http404('The stored file is unavailable.')
    size = field.size if preview else version.byte_size
    content_type = 'image/webp' if preview else version.mime_type
    ranges = request.headers.get('Range')
    if ranges and not preview:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)',ranges)
        try:
            if not match or not any(match.groups()):
                raise ValueError
            if match[1]:
                start = int(match[1]); end = min(int(match[2]) if match[2] else size-1,size-1)
            else:
                start = max(0,size-int(match[2])); end = size-1
            if not 0 <= start <= end < size:
                raise ValueError
        except ValueError:
            stream.close()
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response
        def chunks():
            try:
                stream.seek(start)
                left = end-start+1
                while left:
                    block = stream.read(min(left,64*1024))
                    if not block:
                        break
                    left -= len(block)
                    yield block
            finally:
                stream.close()
        response = StreamingHttpResponse(chunks(),status=206,content_type=content_type)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(end-start+1)
    else:
        response = FileResponse(stream, as_attachment=not preview, filename=version.original_name, content_type=content_type)
        response['Content-Length'] = str(size)
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
