from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from rest_framework.generics import get_object_or_404
from django.utils import timezone
from artists.models import Artist, ArtistMembership
from catalog.models import PlatformObject
from .access import require_editor, grant_created
from .forms import make_form
from .models import EditorialChange, OperationRemoval
from .querysets import scoped
from .resources import resource


class StaleEdit(ValidationError):
    pass


def _check_version(token, instance):
    expected = [instance._meta.label_lower,str(instance.pk),str(instance.updated_at),getattr(instance,'revision',None)]
    try:
        actual = signing.loads(token, salt='portal-edit', max_age=86400)
    except (signing.BadSignature, TypeError):
        raise StaleEdit('Reload this page before saving. The edit form has expired.')
    if actual != expected:
        raise StaleEdit('This record changed since you opened it. Reload to review the latest version.')


def _revoke(obj):
    from publishing.projection import revoke_for_resource
    revoke_for_resource(obj)


def _retired_match(user,artist,kind,form):
    """Reuse a removed relationship's stable UUID when a unique slot is recreated."""
    from django.db.models import UniqueConstraint
    spec=resource(kind)
    retired=scoped(user,artist,kind,write=True,include_removed=True).exclude(pk__in=scoped(user,artist,kind,write=True).values('pk'))
    for constraint in spec.model._meta.constraints:
        if not isinstance(constraint,UniqueConstraint):
            continue
        names=constraint.fields
        if not names:
            if kind!='aliases':
                continue
            names=('artist','name')
        values={}
        for name in names:
            value=getattr(form.instance,name,None)
            if value is None:
                break
            values[name+'__iexact' if kind=='aliases' and name=='name' else name]=value
        else:
            candidates=retired.filter(**values)
            if constraint.condition:
                if not constraint.condition.check({field.name:getattr(form.instance,field.attname) for field in spec.model._meta.concrete_fields}):
                    continue
                candidates=candidates.filter(constraint.condition)
            candidate=candidates.order_by('pk').first()
            if candidate:
                return candidate
    return None


@transaction.atomic
def mutate(user, artist, kind, data, files=None, object_id=None):
    artist = Artist.objects.select_for_update().get(pk=artist.pk,archived_at__isnull=True)
    if not user.is_staff:
        membership = ArtistMembership.objects.select_for_update().filter(artist=artist,user=user).first()
        if not membership:
            raise PermissionDenied('Artist access was removed.')
    require_editor(user,artist)
    if not user.is_staff:
        list(artist.resource_grants.select_for_update().values_list('id',flat=True))
    spec = resource(kind)
    if kind == 'artists' and object_id is None:
        raise PermissionDenied('Artist identities are created by your operator.')
    instance = get_object_or_404(scoped(user,artist,kind,write=True).select_for_update(),pk=object_id) if object_id is not None else None
    if instance:
        _check_version(data.get('version'),instance)
    form = make_form(kind,user,artist,data=data,files=files,instance=instance)
    restored=False
    if not form.is_valid():
        candidate=_retired_match(user,artist,kind,form) if instance is None else None
        if candidate:
            form=make_form(kind,user,artist,data=data,files=files,instance=candidate)
            if not form.is_valid():
                return None,form
            restored=True
        else:
            return None,form
    obj = form.save(commit=False)
    changed_fields = set(form.changed_data) - {'version'}
    if kind in ('recording-links','release-links'):
        current = instance.platform_object if instance else None
        if current and current.service_id == form.cleaned_data['service'].pk and current.url == form.cleaned_data['url']:
            obj.platform_object = current
        else:
            obj.platform_object, _ = PlatformObject.objects.get_or_create(service=form.cleaned_data['service'],object_type='recording' if kind=='recording-links' else 'release',external_id='',url=form.cleaned_data['url'])
        if changed_fields:
            obj.review_status = 'pending'
    if kind == 'profile-links' and changed_fields:
        obj.verification_status = 'unverified'
        obj.verified_at = None
    if not user.is_staff and kind in ('recording-identifiers','recording-works','work-identifiers') and changed_fields:
        obj.verification_status='unverified'
    if not user.is_staff and kind=='works' and 'iswc' in form.changed_data:
        obj.status='unverified'
    if not user.is_staff and kind=='registrations' and changed_fields:
        obj.registration_status='unverified'
        obj.last_verified_at=None
    if kind == 'pages':
        content = dict(obj.content_json or {})
        content['editorial'] = {'text':form.cleaned_data['editorial_text']}
        obj.content_json = content
    if (instance or restored) and hasattr(obj,'revision'):
        obj.revision += 1
    if restored and hasattr(obj,'archived_at'):
        obj.archived_at=None
    asset_rights_changed=kind=='assets' and (bool(form.cleaned_data.get('upload')) or bool({'rights_basis','visibility'} & set(form.changed_data)))
    if asset_rights_changed:
        obj.rights_approved = False
    obj.full_clean()
    obj.save()
    if restored:
        OperationRemoval.objects.select_for_update().filter(model_label=obj._meta.label_lower,object_id=str(obj.pk),restored_at__isnull=True).update(restored_at=timezone.now(),restored_by=user)
    if not instance:
        grant_created(artist,obj)
    if kind == 'assets' and form.cleaned_data.get('upload'):
        from .media import save_upload
        save_upload(obj,form.cleaned_data['upload'],user,artist)
    if asset_rights_changed:
        _revoke(obj)
    EditorialChange.objects.create(organization=artist.organization,artist=artist,user=user,model_label=obj._meta.label_lower,object_id=str(obj.pk),action='restore' if restored else 'update' if instance else 'create',changed_fields=[key for key in form.changed_data if key not in ('version','upload')])
    return obj,form


@transaction.atomic
def remove(user, artist, kind, object_id, version):
    artist = Artist.objects.select_for_update().get(pk=artist.pk,archived_at__isnull=True)
    if not user.is_staff and not ArtistMembership.objects.select_for_update().filter(artist=artist,user=user).exists():
        raise PermissionDenied('Artist access was removed.')
    require_editor(user,artist)
    if kind=='artists' and not user.is_staff and not artist.memberships.filter(user=user,role='owner').exists():
        raise PermissionDenied('Only an artist owner or staff can archive the workspace.')
    if not user.is_staff:
        list(artist.resource_grants.select_for_update().values_list('id',flat=True))
    obj = get_object_or_404(scoped(user,artist,kind,write=True).select_for_update(),pk=object_id)
    public_parents={'profile-links':'artist','artist-assets':'artist','recording-credits':'recording','release-credits':'release','recording-genres':'recording','tracks':'release','recording-assets':'recording','release-assets':'release','recording-links':'recording','release-links':'release','destinations':'fanlink_page'}
    if kind in public_parents:
        _revoke(getattr(obj,public_parents[kind]))
    _check_version(version,obj)
    _revoke(obj)
    if hasattr(obj,'archived_at'):
        obj.archived_at = timezone.now()
        obj.revision += 1
        obj.save()
    EditorialChange.objects.create(organization=artist.organization,artist=artist,user=user,model_label=obj._meta.label_lower,object_id=str(obj.pk),action='delete')
    return obj
