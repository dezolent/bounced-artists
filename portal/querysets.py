from django.db.models import Q
from artists import models as a
from catalog import models as c
from rights import models as r
from events import models as e
from publishing import models as p
from .access import assigned
from .resources import resource


def active_rows(rows):
    """Preserve imported child rows while removing them from editorial surfaces."""
    from django.db.models import CharField, OuterRef, Subquery, Exists
    from django.db.models.functions import Cast
    from .models import EditorialChange, OperationRemoval
    latest=EditorialChange.objects.filter(model_label=rows.model._meta.label_lower,object_id=Cast(OuterRef('pk'),CharField()),action__in=['delete','restore']).order_by('-created_at','-pk').values('action')[:1]
    removed=OperationRemoval.objects.filter(model_label=rows.model._meta.label_lower,object_id=Cast(OuterRef('pk'),CharField()),restored_at__isnull=True)
    return rows.annotate(portal_last_action=Subquery(latest,output_field=CharField()),operation_removed=Exists(removed)).filter(Q(portal_last_action__isnull=True)|~Q(portal_last_action='delete'),operation_removed=False)


def scoped(user, artist, kind, *, write=False,include_removed=False):
    spec = resource(kind)
    model = spec.model
    if kind == 'artists':
        rows=a.Artist.objects.filter(pk=artist.pk)
        return rows if include_removed else rows.filter(archived_at__isnull=True)
    if spec.grant:
        rows=assigned(user,artist,model,spec.grant,write=write,include_archived=include_removed)
        return rows if include_removed else active_rows(rows)
    rows = model.objects.all() if include_removed else active_rows(model.objects.all())
    if hasattr(model, 'archived_at') and not include_removed:
        rows = rows.filter(archived_at__isnull=True)
    if spec.artist_field:
        rows = rows.filter(**{spec.artist_field: artist})
        if kind == 'contacts' and not user.is_staff:
            rows = rows.exclude(visibility='private')
        if kind == 'artist-assets':
            rows = rows.filter(asset__in=scoped(user, artist, 'assets', write=write))
        if kind=='artist-parties':
            rows=rows.filter(party__in=scoped(user,artist,'parties',write=write))
        return rows
    if kind in {'pages', 'fanlinks'}:
        rows = rows.filter(website__artist=artist, website__organization=artist.organization, website__archived_at__isnull=True)
        if kind == 'fanlinks':
            rows = rows.filter(Q(recording__in=scoped(user, artist, 'recordings', write=write)) | Q(release__in=scoped(user, artist, 'releases', write=write)))
        return rows
    parent_kind = {'recording':'recordings', 'from_recording':'recordings', 'release':'releases', 'release_group':'release-groups', 'work':'works', 'party':'parties', 'event':'events', 'fanlink_page':'fanlinks', 'page':'pages'}.get(spec.parent)
    if spec.parent == 'registration':
        parents = r.WorkRegistration.objects.filter(work__in=scoped(user, artist, 'works', write=write))
    elif parent_kind:
        parents = scoped(user, artist, parent_kind, write=write)
    else:
        return rows.none()
    rows = rows.filter(**{f'{spec.parent}__in': parents})
    if kind in {'contributors','claims'}:
        rows = rows.filter(party__in=scoped(user, artist, 'parties', write=write))
    if kind == 'claims' and write:
        rows = rows.filter(source_record__isnull=True)
    if kind == 'recording-works':
        rows = rows.filter(work__in=scoped(user, artist, 'works', write=write))
    secondary={'tracks':('recording','recordings'),'recording-relations':('to_recording','recordings'),'recording-assets':('asset','assets'),'release-assets':('asset','assets'),'recording-labels':('label','labels'),'release-labels':('label','labels')}
    if kind in secondary:
        field,target_kind=secondary[kind]
        rows=rows.filter(**{f'{field}__in':scoped(user,artist,target_kind,write=write)})
    if kind=='seo':
        rows=rows.filter(Q(og_asset__isnull=True)|Q(og_asset__in=scoped(user,artist,'assets',write=write)))
    return rows


def choices(user, artist, model, *,write=True):
    model_kinds = {a.Artist:'artists', c.Recording:'recordings', c.Release:'releases', c.Label:'labels', c.ReleaseGroup:'release-groups', c.Asset:'assets', r.MusicalWork:'works', r.Party:'parties', e.Event:'events', p.FanlinkPage:'fanlinks', p.Page:'pages'}
    if model == a.Artist:
        # Credits are labels, not access grants; same-organization collaborators are selectable.
        return model.objects.filter(organization=artist.organization, archived_at__isnull=True)
    if model in model_kinds:
        return scoped(user, artist, model_kinds[model], write=write)
    if model == p.Website:
        return model.objects.filter(artist=artist, organization=artist.organization, archived_at__isnull=True)
    if model == r.WorkRegistration:
        return model.objects.filter(work__in=scoped(user, artist, 'works', write=write))
    if model == c.Medium:
        return scoped(user, artist, 'media', write=write)
    if model == c.PlatformObject:
        recordings = scoped(user, artist, 'recordings', write=write)
        releases = scoped(user, artist, 'releases', write=write)
        return model.objects.filter(Q(recordingplatformmatch__recording__in=recordings) | Q(releaseplatformmatch__release__in=releases)).distinct()
    if model in {a.Service, c.Genre, r.RightsSociety}:
        return model.objects.all()
    return model.objects.none()
