"""Fill missing private catalog metadata from an already authorized track receipt.

No downloads, title-based joins, rights approvals or public publication occur here.
"""
from datetime import date
import json
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.contrib.admin.models import LogEntry, CHANGE
from django.utils.text import slugify

from artists.models import ArtistResourceGrant
from catalog.models import Asset, Genre, RecordingAsset, RecordingGenre, RecordingIdentifier, ReleaseAsset, ReleaseTrack
from portal.models import EditorialChange
from portal.querysets import active_rows
from portal.validation import safe_external_url
from provenance.models import AuditEvent, EntityTarget, FieldObservation, EditorialOverride


def reported_fields(payload, platform):
    album = payload.get('album')
    album = album if isinstance(album, dict) else {}
    fields = {}
    duration = payload.get('duration_ms')
    if duration is None and platform == 'soundcloud':
        duration = payload.get('duration')
    if type(duration) is int and 0 <= duration <= 2147483647:
        fields['duration_ms'] = duration
    explicit = payload.get('explicit', payload.get('is_explicit'))
    if type(explicit) is bool:
        fields['explicit'] = explicit
    language = payload.get('audio_language') or payload.get('language')
    if isinstance(language, str) and re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language) and len(language) <= 30:
        fields['language'] = language
    raw_date = payload.get('release_date') or album.get('release_date')
    if isinstance(raw_date, str):
        precision = {4: 'year', 7: 'month', 10: 'day'}.get(len(raw_date))
        try:
            if precision and re.fullmatch(r'\d{4}(?:-\d{2}){0,2}', raw_date):
                parsed = date.fromisoformat(raw_date + ('-01-01' if precision == 'year' else '-01' if precision == 'month' else ''))
                declared = payload.get('release_date_precision') or album.get('release_date_precision')
                precision_order = {'year':0,'month':1,'day':2}
                if isinstance(declared,str) and declared in precision_order and precision_order[declared] < precision_order[precision]:
                    precision = declared
                fields.update(original_release_date=parsed, date_precision=precision)
        except ValueError:
            pass
    return fields


def artwork(payload, platform):
    album = payload.get('album')
    album = album if isinstance(album, dict) else {}
    hosts = {'spotify': ('scdn.co', 'spotifycdn.com'), 'apple-music': ('mzstatic.com',), 'soundcloud': ('sndcdn.com',)}.get(platform, ())
    for candidate in (payload.get('artwork'), payload.get('artwork_url'), payload.get('images'), album.get('artwork'), album.get('images')):
        candidates = candidate if isinstance(candidate, list) else [candidate]
        for item in candidates:
            image = item if isinstance(item, dict) else {}
            url = image.get('link') or image.get('url') or (item if isinstance(item, str) else '')
            if not isinstance(url,str):
                continue
            try:
                url = safe_external_url(url)
                parsed = urlsplit(url)
            except (ValidationError, ValueError, TypeError):
                continue
            if not url or len(url) > 2048 or not any(parsed.hostname == host or (parsed.hostname or '').endswith('.' + host) for host in hosts):
                continue
            suffix = parsed.path.lower().rsplit('.', 1)[-1]
            mime = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','avif':'image/avif'}.get(suffix)
            if mime is None and platform == 'spotify' and parsed.hostname == 'i.scdn.co':
                mime = 'image/jpeg'
            if mime:
                dimensions = {key:value for key in ('width','height') if type(value := image.get(key)) is int and 0 < value <= 2147483647}
                return {'source_url':url, 'mime_type':mime, **dimensions}
    return None


def edited_fields(recording, targets):
    """Honor private portal, Operations and Django admin edit histories."""
    touched = set()
    for fields in EditorialChange.objects.filter(model_label='catalog.recording', object_id=str(recording.pk)).values_list('changed_fields', flat=True):
        if isinstance(fields,list):
            touched.update(field for field in fields if isinstance(field,str))
    for fields in AuditEvent.objects.filter(organization=recording.organization,
            action='portal.operations.update',target_label='catalog.recording',
            change_json__object_id=str(recording.pk)).values_list('change_json__changed_fields',flat=True):
        if isinstance(fields,list):
            touched.update(field for field in fields if isinstance(field,str))
    names = {}
    for field in recording._meta.fields:
        names[field.name.casefold()] = field.name
        names[str(field.verbose_name).casefold()] = field.name
    for message in LogEntry.objects.filter(content_type__app_label='catalog',content_type__model='recording',
            object_id=str(recording.pk),action_flag=CHANGE).values_list('change_message',flat=True):
        if message.startswith('Portal operations:'):
            # Structured AuditEvent history above covers these entries.
            continue
        try:
            entries = json.loads(message)
        except (TypeError,ValueError):
            entries = None
        if not isinstance(entries,list):
            # Older free-form logs cannot identify which empty fields were deliberate.
            touched.update(field.name for field in recording._meta.fields)
            continue
        for entry in entries:
            changed = entry.get('changed') if isinstance(entry,dict) else None
            # Inline changes name a related object; they are not recording fields.
            if not isinstance(changed,dict) or changed.get('name') or changed.get('object'):
                continue
            fields = changed.get('fields')
            if isinstance(fields,list):
                touched.update(names[name.casefold()] for name in fields if isinstance(name,str) and name.casefold() in names)
    touched.update(EditorialOverride.objects.filter(target__in=targets).values_list('field_name',flat=True))
    return touched


def enrich(recording, conn, source, *, report=None):
    """Caller holds the platform identity lock and has checked active grants/ownership."""
    report = report if report is not None else {}
    payload = source.payload_json
    targets = EntityTarget.objects.filter(organization=recording.organization, recording=recording)
    target = targets.order_by('created_at', 'pk').first()
    if target is None:
        target = EntityTarget.objects.create(organization=recording.organization, recording=recording)
    touched = edited_fields(recording, targets)
    reported = reported_fields(payload, conn.service.slug)
    evidence = {field:value for field,value in reported.items() if field in {'duration_ms','explicit','original_release_date'}}
    for field,value in evidence.items():
        FieldObservation.objects.get_or_create(source_record=source,target=target,field_name=field,
            defaults={'value_json':value.isoformat() if isinstance(value,date) else value,'confidence':'1.000'})
    changed = []
    for field,value in reported.items():
        if field == 'date_precision':
            continue
        if field in touched or getattr(recording,field) not in (None, ''):
            continue
        if field == 'original_release_date' and 'date_precision' in touched:
            continue
        setattr(recording,field,value)
        changed.append(field)
        if field == 'original_release_date':
            recording.date_precision = reported['date_precision']
            changed.append('date_precision')
    if changed:
        recording.full_clean()
        recording.revision += 1
        recording.save(update_fields=[*changed,'revision','updated_at'])
        report['fields_filled'] = report.get('fields_filled',0) + len(changed)
    # An existing decision, including a removed/rejected identifier, is never bypassed.
    external_ids = payload.get('external_ids')
    external_ids = external_ids if isinstance(external_ids, dict) else {}
    isrc = payload.get('isrc') or external_ids.get('isrc')
    if isinstance(isrc,str) and re.fullmatch(r'[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}',isrc) and not RecordingIdentifier.objects.filter(recording=recording,scheme__iexact='isrc').exists():
        identifier = RecordingIdentifier(recording=recording,scheme='isrc',value=isrc.upper(),source_record=source,verification_status='unverified')
        identifier.full_clean()
        identifier.save()
        report['identifiers_added'] = report.get('identifiers_added',0) + 1
    # Do not add around existing editorial classifications, including removals.
    if not RecordingGenre.objects.filter(recording=recording).exists():
        values = payload.get('genres') or ([payload['genre']] if payload.get('genre') else [])
        if isinstance(values,list):
            for name in dict.fromkeys(value.strip() for value in values if isinstance(value,str) and value.strip() and len(value) <= 100):
                key = slugify(name)[:50]
                if not key:
                    continue
                genre, _ = Genre.objects.get_or_create(slug=key,defaults={'name':name})
                if genre.name.casefold() == name.strip().casefold() and active_rows(Genre.objects.filter(pk=genre.pk)).exists():
                    _, created = RecordingGenre.objects.get_or_create(recording=recording,genre=genre)
                    report['genres_added'] = report.get('genres_added',0) + int(created)
    # Any song/release cover relation reserves the editorial choice, even if removed.
    release_ids = ReleaseTrack.objects.filter(recording=recording).values('release_id')
    if RecordingAsset.objects.filter(recording=recording,purpose='cover').exists() or ReleaseAsset.objects.filter(release_id__in=release_ids,purpose='cover').exists():
        return
    image = artwork(payload,conn.service.slug)
    if image is None:
        return
    asset = Asset(organization=recording.organization,category=Asset.Category.COVER_ART,
        display_name=f'{recording.title[:180]} cover',visibility='private',rights_approved=False,
        rights_basis='Platform-reported artwork; rights unverified',
        description=f'Reported by {conn.service.name}. Source record: {source.pk}. External reference; no file downloaded.',**image)
    asset.full_clean()
    asset.save()
    ArtistResourceGrant.objects.create(artist=conn.artist,asset=asset,capability='manage')
    RecordingAsset.objects.create(recording=recording,asset=asset,purpose='cover',is_primary=True)
    report['covers_added'] = report.get('covers_added',0) + 1
