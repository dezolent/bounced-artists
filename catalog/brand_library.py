"""Application-owned, versioned brand artwork; never private artist uploads.

Use brand_logo() anywhere in the application. Only the explicitly packaged WebP
files are exposed. Imported originals remain source evidence, outside artist CRUD.
"""
from django.db.models import Q
from django.templatetags.static import static


BRANDS = {
    'amazon-music':'Amazon Music', 'anghami':'Anghami', 'apple-music':'Apple Music',
    'audiomack':'Audiomack', 'bandcamp':'Bandcamp', 'beatport':'Beatport',
    'deezer':'Deezer', 'facebook':'Facebook', 'iheartradio':'iHeartRadio',
    'instagram':'Instagram', 'itunes-store':'iTunes Store', 'pandora':'Pandora',
    'soundcloud':'SoundCloud', 'spotify':'Spotify', 'tidal':'TIDAL',
    'tiktok':'TikTok', 'x':'X', 'youtube':'YouTube', 'youtube-music':'YouTube Music',
}
PLATFORMS = ('amazon-music','anghami','apple-music','audiomack','bandcamp','beatport','deezer','itunes-store','pandora','soundcloud','spotify','tidal','youtube','youtube-music')
SOCIAL = tuple(key for key in BRANDS if key!='itunes-store')
GROUPS = {'social':SOCIAL, 'platforms':PLATFORMS}
LIBRARY_CATEGORIES = [('social_logo','Social logos'),('platform_logo','Platform logos')]


def brand_logo(slug, group='platforms'):
    slug=slug.lower().replace('_','-').replace(' ','-')
    slug={'titkok':'tiktok','itunes':'itunes-store','applemusic':'apple-music','amazon':'amazon-music','twitter':'x'}.get(slug,slug)
    if slug not in GROUPS.get(group,()):
        return ''
    return static(f'portal/library/{group}/{slug}.webp')


def library_rows():
    return [dict(label=BRANDS[slug],category='social_logo' if group=='social' else 'platform_logo',
                 category_label='Social logos' if group=='social' else 'Platform logos',
                 cover_url=brand_logo(slug,group),detail_url=brand_logo(slug,group),
                 subtitle=f'{"Social" if group=="social" else "Fanlink platform"} · WebP',icon='assets',status='',shared=True)
            for group,slugs in GROUPS.items() for slug in slugs]


def legacy_brand_paths():
    paths=[]
    for group,slugs in GROUPS.items():
        for slug in slugs:
            paths.append(f'public/images/{group}/{slug}.webp')
            if slug=='tiktok':
                paths.append(f'public/images/{group}/titkok.webp')
    return paths


def legacy_brand_query():
    # Exact known imported directories/names; an artist's similarly named
    # personal logo elsewhere never becomes a shared library reference.
    return ~Q(source_repository='') & Q(source_path__in=legacy_brand_paths())
