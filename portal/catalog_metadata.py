"""Consolidate known platform facts for display without changing editorial catalog rows."""
from datetime import date

from .insights import body_for

PRIORITY = {'spotify': 0, 'apple-music': 1, 'soundcloud': 2, 'shazam': 3, 'musixmatch': 4, 'youtube': 5, 'tiktok': 6}


def _text(value):
    if isinstance(value, str):
        return value.strip()[:2000]
    if isinstance(value, dict):
        return _text(value.get('name') or value.get('text') or value.get('p_line') or value.get('c_line'))
    if isinstance(value, list):
        return ', '.join(filter(None, (_text(v) for v in value[:30])))
    return ''


def consolidated_metadata(recording, groups, *, has_labels=False, has_genres=False, has_releases=False, identifiers=()):
    candidates = [row['source'] for group in groups for row in group.get('catalog_metadata', [])]
    candidates.sort(key=lambda s: (PRIORITY.get(s.service.slug, 99), s.collector != 'spotify-web-api', -s.observed_at.timestamp(), str(s.pk)))
    chosen = {}
    aliases = {'version_title': ('version_title',), 'language': ('audio_language', 'language'),
        'composer': ('composer_name', 'composers'), 'copyright': ('copyright', 'copyrights'),
        'album_name': ('album_name',), 'audio_traits': ('audio_traits',), 'license': ('license',),
        'label': ('record_label', 'label'), 'genres': ('genres', 'genre'), 'isrc': ('isrc',),
        'release_date': ('release_date',), 'duration_ms': ('duration_ms',),
        'explicit': ('explicit', 'is_explicit'), 'track_number': ('track_number',), 'disc_number': ('disc_number',)}
    for source in candidates:
        body = body_for(source)
        album = body.get('album')
        if isinstance(album, dict):
            body = {'album_name': album.get('name') or album.get('title'), **{key: album[key] for key in ('release_date', 'release_date_precision', 'label', 'record_label', 'copyright', 'copyrights') if key in album}, **body}
        for field, keys in aliases.items():
            if field in chosen:
                continue
            for key in keys:
                value = body.get(key)
                if value is None and field == 'isrc' and isinstance(body.get('external_ids'), dict):
                    value = body['external_ids'].get('isrc')
                valid = (type(value) is int and value >= 0) if field in {'duration_ms', 'track_number', 'disc_number'} else type(value) is bool if field == 'explicit' else bool(_text(value))
                if valid:
                    chosen[field] = {'value': value if field in {'duration_ms', 'track_number', 'disc_number', 'explicit'} else _text(value), 'source': source}
                    break
    fallback = {field: chosen[field]['value'] for field in ('version_title', 'language', 'explicit', 'duration_ms') if field in chosen and getattr(recording, field, None) in (None, '')}
    if recording.original_release_date is None and 'release_date' in chosen:
        raw = chosen['release_date']['value']
        try:
            fallback['date'] = date.fromisoformat(raw if len(raw) == 10 else raw + ('-01-01' if len(raw) == 4 else '-01'))
            fallback['date_precision'] = 'year' if len(raw) == 4 else 'month' if len(raw) == 7 else 'day'
        except ValueError:
            pass
    rows = []
    excluded = {'version_title', 'language', 'explicit', 'duration_ms', 'release_date'}
    if has_labels:
        excluded.add('label')
    if has_genres:
        excluded.add('genres')
    if has_releases:
        excluded.add('album_name')
    if any(row.scheme.lower() == 'isrc' for row in identifiers):
        excluded.add('isrc')
    for field, label in (('album_name', 'Album'), ('audio_traits', 'Audio format'), ('license', 'License'), ('composer', 'Composer'), ('label', 'Record label'), ('genres', 'Genres'), ('isrc', 'ISRC'), ('copyright', 'Copyright'), ('track_number', 'Track number'), ('disc_number', 'Disc number')):
        if field in chosen and field not in excluded:
            claim = chosen[field]
            rows.append({'label': label, 'value': claim['value'], 'service': claim['source'].service.name, 'source': claim['source']})
    return {'fallback': fallback, 'rows': rows, 'sources': chosen}
