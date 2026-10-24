"""Presentation of validated local files. No remote file fetching or HTML rendering."""
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from catalog.models import Asset

TEXT_LIMIT=256*1024
TEXT_SUFFIXES={'.txt','.csv','.srt','.vtt','.json','.webmanifest','.md','.xml','.yaml','.yml','.toml','.css','.js','.html'}


def asset_filename(asset):
    if asset.current_version_id:
        return asset.current_version.original_name.lower()
    return PurePosixPath(asset.source_path or urlsplit(asset.source_url).path or asset.display_name).name.lower()


def is_text(asset):
    mime=(asset.current_version.mime_type if asset.current_version_id else asset.mime_type).split(';')[0].lower()
    return asset_filename(asset) in {'api-catalog','.gitkeep'} or mime.startswith('text/') or mime in {'application/json','application/manifest+json','application/x-subrip','application/xml'} or PurePosixPath(asset_filename(asset)).suffix in TEXT_SUFFIXES


def suggested_category(asset):
    name=asset_filename(asset)
    path=asset.source_path.lower()
    if name.endswith(('.srt','.vtt')):
        return Asset.Category.LYRICS_SRT
    if '/lyrics/' in path or 'lyrics' in name:
        return Asset.Category.LYRICS_TEXT
    if '/covers/' in path:
        return Asset.Category.COVER_ART
    if name.startswith(('favicon','apple-touch-icon','android-chrome')):
        return Asset.Category.ARTIST_ICON
    if '/logos/' in path or 'logo' in name:
        return Asset.Category.ARTIST_LOGO
    if 'banner' in name:
        return Asset.Category.BANNER_IMAGE
    if 'profile' in name or 'portrait' in name:
        return Asset.Category.PROFILE_IMAGE
    if name in {'robots.txt','ads.txt','llms.txt','site.webmanifest'} or name.endswith(('.woff','.woff2','.ttf','.otf')):
        return Asset.Category.WEBSITE_ASSET
    if asset.mime_type.startswith('audio/'):
        return Asset.Category.AUDIO
    if asset.mime_type.startswith('video/'):
        return Asset.Category.VIDEO
    if is_text(asset) or asset.mime_type=='application/pdf':
        return Asset.Category.DOCUMENT
    return ''


def asset_icon(asset):
    if asset_filename(asset)=='robots.txt':
        return 'robot'
    if asset.category in {Asset.Category.LYRICS_TEXT,Asset.Category.LYRICS_SRT} or suggested_category(asset) in {Asset.Category.LYRICS_TEXT,Asset.Category.LYRICS_SRT}:
        return 'lyrics'
    if is_text(asset):
        return 'file-text'
    if asset.mime_type.startswith('audio/'):
        return 'catalog'
    return 'file'


def text_preview(asset):
    if not is_text(asset):
        return None
    result={'text':'','truncated':False,'unavailable':True}
    if asset.source_repository == 'source-record' and not asset.current_version_id:
        from provenance.collectors.lyrics import source_lyric_preview
        text = source_lyric_preview(asset)
        if text is not None:
            result.update(text=text, unavailable=False)
        return result
    if not asset.current_version_id or not asset.current_version.file:
        return result
    try:
        with asset.current_version.file.open('rb') as stream:
            data=stream.read(TEXT_LIMIT+1)
        if b'\x00' in data:
            return result
        result.update(text=data[:TEXT_LIMIT].decode('utf-8-sig',errors='replace'),truncated=len(data)>TEXT_LIMIT,unavailable=False)
    except (OSError,ValueError):
        pass
    return result
