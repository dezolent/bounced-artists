"""Explicit private catalog presentation; never fetch remote media."""
from django.urls import reverse
from .access import can_edit
from .querysets import scoped
from .resources import resource


def duration(value):
    if value is None:
        return 'Not set'
    seconds = value // 1000
    minutes, seconds = divmod(seconds,60)
    hours, minutes = divmod(minutes,60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


def details(request,artist,kind,obj):
    from .views import asset_context, cover_url, decorate, platform_links, set_credit_display
    from .asset_display import text_preview, asset_icon
    from .validation import safe_external_url
    from django.core.exceptions import ValidationError
    from catalog.brand_library import brand_logo
    stem = 'recording' if kind == 'recordings' else 'release'
    set_credit_display(obj)
    editable = can_edit(request.user,artist) and scoped(request.user,artist,kind,write=True).filter(pk=obj.pk).exists()
    edit_url = reverse('portal:edit',args=[artist.pk,kind,obj.pk]) if editable else ''
    date = obj.original_release_date if stem == 'recording' else obj.release_date
    identifiers = list(scoped(request.user,artist,stem+'-identifiers').filter(**{stem:obj}))
    labels = scoped(request.user,artist,stem+'-labels').filter(**{stem:obj}).select_related('label').order_by('position','pk')
    genres = scoped(request.user,artist,stem+'-genres').filter(**{stem:obj}).select_related('genre')
    assets = asset_context(request,artist,resource(stem+'-assets').model.objects.filter(**{stem:obj}))
    for asset in assets:
        asset.edit_url = edit_url+'#assets' if edit_url and scoped(request.user,artist,'assets',write=True).filter(pk=asset.pk).exists() else ''
        asset.text_preview, asset.icon = text_preview(asset), asset_icon(asset)
    fanlinks = list(scoped(request.user,artist,'fanlinks').filter(**{stem:obj}).select_related('website').order_by('title','pk'))
    for fanlink in fanlinks:
        decorate(request,artist,fanlink,'fanlinks')
        fanlink.destinations_display = []
        for row in scoped(request.user,artist,'destinations').filter(fanlink_page=fanlink).select_related('platform_object__service').order_by('sort_order','pk'):
            try:
                url = safe_external_url(row.platform_object.url)
            except ValidationError:
                url = ''
            fanlink.destinations_display.append({'label':row.label or row.platform_object.service.name,'service':row.platform_object.service.name,'url':url,'enabled':row.enabled,'logo_url':brand_logo(row.platform_object.service.slug,'social') or brand_logo(row.platform_object.service.slug)})
    metadata = [('Title',obj.title),('Type',getattr(obj,stem+'_type'))]
    for name in ('version_title','edition','country','language','script','packaging','barcode','catalog_number','disambiguation','notes'):
        if hasattr(obj,name):
            metadata.append((name.replace('_',' ').capitalize(),getattr(obj,name)))
    work_links, releases, tracks, derived_labels, media, group = [], [], [], [], [], None
    if stem == 'recording':
        metadata.append(('Explicit content','Not set' if obj.explicit is None else 'Yes' if obj.explicit else 'No'))
        work_links = list(scoped(request.user,artist,'recording-works').filter(recording=obj).select_related('work'))
        release_ids = scoped(request.user,artist,'tracks').filter(recording=obj).values('release_id')
        releases = [decorate(request,artist,row,'releases') for row in scoped(request.user,artist,'releases').filter(pk__in=release_ids)]
        derived_labels = scoped(request.user,artist,'release-labels').filter(release__in=releases).select_related('label','release')
        total_duration, incomplete = obj.duration_ms, False
    else:
        tracks = list(scoped(request.user,artist,'tracks').filter(release=obj).select_related('recording','medium').order_by('disc_number','track_number','pk'))
        for track in tracks:
            decorate(request,artist,track.recording,'recordings')
            track.duration_display = duration(track.duration_ms if track.duration_ms is not None else track.recording.duration_ms)
        lengths = [track.duration_ms if track.duration_ms is not None else track.recording.duration_ms for track in tracks]
        total_duration = sum(value for value in lengths if value is not None) if any(value is not None for value in lengths) else None
        incomplete = not tracks or any(value is None for value in lengths)
        work_links = list(scoped(request.user,artist,'recording-works').filter(recording_id__in=[track.recording_id for track in tracks]).select_related('work','recording'))
        media = scoped(request.user,artist,'media').filter(release=obj).order_by('position')
        if obj.release_group_id:
            group = scoped(request.user,artist,'release-groups').filter(pk=obj.release_group_id).first()
        if obj.label_party_id:
            legacy = scoped(request.user,artist,'parties').filter(pk=obj.label_party_id).first()
            if legacy:
                metadata.append(('Label (legacy)',legacy.display_name))
    for link in work_links:
        link.detail_url = reverse('portal:detail',args=[artist.pk,'works',link.work.pk])
        link.identifiers_display = list(scoped(request.user,artist,'work-identifiers').filter(work=link.work))
        link.contributors_display = list(scoped(request.user,artist,'contributors').filter(work=link.work).select_related('party'))
    return dict(item=obj,item_kind=stem,recording=obj if stem=='recording' else None,release=obj if stem=='release' else None,
                cover_url=cover_url(request,artist,obj),edit_url=edit_url,can_edit=editable,date=date,metadata=metadata,
                duration_display=duration(total_duration),duration_incomplete=incomplete,labels=labels,derived_labels=derived_labels,genres=genres,identifiers=identifiers,
                credits=scoped(request.user,artist,stem+'-credits').filter(**{stem:obj}).select_related('artist').order_by('billing_order','pk'),
                work_links=work_links,releases=releases,tracks=tracks,media=media,release_group=group,assets=assets,fanlinks=fanlinks,
                platform_links=platform_links(request,artist,stem+'-links',obj),delete_url=reverse('portal:delete',args=[artist.pk,kind,obj.pk]) if editable else '')
