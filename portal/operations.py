"""Staff operations screens with explicit contracts and guarded, audited writes."""
from functools import wraps
import hashlib
import json
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from artists.models import Artist, ArtistResourceGrant
from provenance.models import AuditEvent
from .operation_registry import REGISTRY, NO_CREATE, REVOKABLE, REMOVABLE
from .presentation import present
from .services import StaleEdit
from .validation import safe_external_url

TOKEN_SALT = 'portal-operations'


def require_operator(user, spec=None):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        raise PermissionDenied('Staff access is required.')
    if spec and spec.superuser and not user.is_superuser:
        raise PermissionDenied('System administrator access is required.')


def operator_required(view):
    @login_required
    @wraps(view)
    def guarded(request, *args, **kwargs):
        # Recheck the database for reads as well as mutations; session caches must
        # not retain operations access after an operator is deactivated.
        actor = get_user_model().objects.filter(pk=request.user.pk).first()
        if not actor:
            raise PermissionDenied('Staff access was removed.')
        require_operator(actor)
        request.user = actor
        return view(request, *args, **kwargs)
    return guarded


def operation(key, user):
    spec = REGISTRY.get(key)
    if spec is None:
        raise Http404
    require_operator(user, spec)
    return spec


def object_label(obj, depth=0):
    """Readable FK choices without calling arbitrary model __str__ methods."""
    if obj is None:
        return '—'
    spec = REGISTRY.get(obj._meta.label_lower.replace('.', '-'))
    if spec and spec.label == 'sessions.session':
        return 'Session metadata'
    row = present(obj)
    fallback = f'{obj._meta.verbose_name.capitalize()} {str(obj.pk)[:8]}'
    if row['label'] != fallback:
        return row['label']
    for name in ('username', 'name', 'display_name', 'title', 'domain', 'code', 'action', 'issue_type', 'field_name', 'job_type', 'model_label'):
        if spec and name in spec.fields:
            value = getattr(obj, name, None)
            if value:
                return str(value)[:180]
    if depth < 2 and spec:
        pieces = []
        for name in spec.fields:
            field = obj._meta.get_field(name)
            if field.is_relation and getattr(obj, field.attname, None):
                pieces.append(object_label(getattr(obj, name), depth + 1))
            if len(pieces) == 2:
                break
        if pieces:
            return ' · '.join(pieces)
    return fallback


def detail_url(obj, user):
    key = obj._meta.label_lower.replace('.', '-')
    spec = REGISTRY.get(key)
    if not spec or spec.label == 'sessions.session' or (spec.superuser and not user.is_superuser):
        return ''
    return reverse('portal:operation-detail', args=[key, str(obj.pk)])


def queryset(spec):
    fields = [name for name in spec.fields if spec.model._meta.get_field(name).is_relation]
    return spec.model.objects.select_related(*fields).order_by('pk')


def scalar(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def display_fields(obj, spec, user):
    result = []
    for name in spec.fields:
        field = obj._meta.get_field(name)
        value = getattr(obj, name)
        url = ''
        if field.is_relation:
            url = detail_url(value, user) if value else ''
            value = object_label(value)
        else:
            if isinstance(field,models.URLField) and value:
                try:
                    safe_external_url(value)
                except ValidationError:
                    value = 'URL withheld: requires a safe public HTTPS address.'
            display = getattr(obj, f'get_{name}_display', None)
            value = scalar(display() if display else value)
        result.append({'name':str(field.verbose_name).capitalize(), 'value':value, 'url':url})
    return result


def version_token(spec, obj, actor):
    names = set(spec.edit_fields) | {'updated_at', 'revision', 'archived_at'}
    values = {name:scalar(getattr(obj, obj._meta.get_field(name).attname)) for name in names if any(f.name == name for f in obj._meta.concrete_fields)} if obj else {}
    if obj and spec.label in REMOVABLE:
        marker = removal_marker(obj)
        values['removal_state'] = [str(marker.pk),str(marker.updated_at),str(marker.restored_at)] if marker else None
        editorial = workspace_removal(obj)
        values['workspace_removal_state'] = [str(editorial.pk),editorial.action,str(editorial.created_at)] if editorial else None
    checksum = hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    return signing.dumps([spec.key,str(obj.pk) if obj else '',actor.pk,checksum], salt=TOKEN_SALT)


def check_version(spec, obj, actor, token):
    try:
        actual = signing.loads(token, salt=TOKEN_SALT, max_age=86400)
        expected = signing.loads(version_token(spec,obj,actor), salt=TOKEN_SALT)
    except (signing.BadSignature, TypeError):
        raise StaleEdit('Reload this form before saving. Its version has expired.') from None
    if actual != expected:
        raise StaleEdit('This record changed. Reload the form and review the latest version.')


def organization_ids(obj, seen=None):
    """Follow explicit organizational anchors, never credits or shared identifiers."""
    if obj is None:
        return set()
    seen = set() if seen is None else seen
    identity = (obj._meta.label_lower,str(obj.pk))
    if identity in seen:
        return set()
    seen.add(identity)
    if obj._meta.label_lower == 'artists.organization':
        return {obj.pk}
    if hasattr(obj, 'organization_id'):
        return {obj.organization_id} if obj.organization_id else set()
    result = set()
    # These forward relationships carry scope; global services, societies and
    # platform objects intentionally do not imply access or ownership.
    for name in ('artist','recording','release','release_group','work','party','event','website','page','fanlink_page','registration','connection','source_record','target','asset','from_recording','to_recording'):
        if getattr(obj, f'{name}_id', None):
            result |= organization_ids(getattr(obj,name),seen)
    return result


def validate_relationships(obj):
    ids = organization_ids(obj)
    for field in obj._meta.concrete_fields:
        if field.is_relation and getattr(obj, field.attname, None):
            ids |= organization_ids(getattr(obj,field.name))
    if len(ids) > 1:
        raise ValidationError('All private relationships must belong to the same organization.')
    if obj._meta.label_lower == 'catalog.releasetrack' and obj.medium_id and obj.medium.release_id != obj.release_id:
        raise ValidationError({'medium':'The medium must belong to this release.'})
    if obj._meta.label_lower == 'catalog.asset' and obj.rights_approved:
        if not obj.rights_basis.strip() or not obj.current_version_id:
            raise ValidationError({'rights_approved':'Upload a file version and provide a rights basis before approving artwork.'})
    if obj._meta.label_lower == 'events.venue' and obj.timezone:
        from artists.validators import validate_timezone
        validate_timezone(obj.timezone)


class RedactedURLInput(forms.URLInput):
    """Never echo imported credentials or an unsafe submitted URL into HTML."""
    def format_value(self,value):
        if value:
            try:
                safe_external_url(str(value))
            except ValidationError:
                return None
        return super().format_value(value)


def removal_marker(obj):
    from .models import OperationRemoval
    return OperationRemoval.objects.filter(model_label=obj._meta.label_lower,object_id=str(obj.pk)).first()


def workspace_removal(obj):
    from .models import EditorialChange
    return EditorialChange.objects.filter(model_label=obj._meta.label_lower,object_id=str(obj.pk),action__in=['delete','restore']).order_by('-created_at','-pk').first()


def is_removed(obj):
    marker = removal_marker(obj)
    workspace_marker = workspace_removal(obj)
    return bool((marker and marker.restored_at is None) or (workspace_marker and workspace_marker.action == 'delete'))


def source_bound(spec,obj):
    return bool(obj and spec.label == 'rights.rightsclaim' and obj.source_record_id)


def require_mutable_record(spec,obj):
    if source_bound(spec,obj):
        raise PermissionDenied('Source-backed rights assertions are preserved evidence. Create a separate editorial assertion instead.')


def make_operation_form(spec, actor, obj=None, data=None):
    require_mutable_record(spec,obj)
    names = list(spec.edit_fields)
    # Organization and relationship identity can be established only at create.
    # Source evidence and optional editorial references may be corrected safely.
    fixed = {'organization','artist','user','connection','recording','release','release_group','work','party','event','website','page','fanlink_page','registration','from_recording','to_recording'}
    if obj:
        names = [name for name in names if name not in fixed]
        if spec.label == 'artists.artistresourcegrant':
            names = ['capability']
    original_unsafe_urls = {}
    for name in names:
        if obj and isinstance(spec.model._meta.get_field(name),models.URLField):
            value = getattr(obj,name,'')
            if value:
                try:
                    safe_external_url(value)
                except ValidationError:
                    original_unsafe_urls[name] = value
    base = forms.modelform_factory(spec.model, fields=names)

    class OperationForm(base):
        version = forms.CharField(widget=forms.HiddenInput)

        def clean(self):
            cleaned = super().clean()
            allowed = set(self.fields) | {'csrfmiddlewaretoken'}
            if self.is_bound and set(self.data) - allowed:
                raise ValidationError('Unexpected fields in this request. Use the fields shown on this form.')
            for name in ('secondary_types','capabilities'):
                if name in cleaned:
                    items = [item.strip() for item in cleaned[name].split(',') if item.strip()]
                    if len(items) > 10 or any(len(item)>30 for item in items):
                        self.add_error(name,'Enter at most 10 comma-separated values, each up to 30 characters.')
                    cleaned[name] = items
            for name, field in self.fields.items():
                if name in original_unsafe_urls and not cleaned.get(name):
                    # Empty redacted controls keep the original stored value;
                    # callers must explicitly submit a safe replacement.
                    cleaned[name] = original_unsafe_urls[name]
                    continue
                if isinstance(field, forms.URLField) and cleaned.get(name):
                    try:
                        safe_external_url(cleaned[name])
                    except ValidationError as error:
                        self.add_error(name,error)
            return cleaned

        def _post_clean(self):
            # Several domain clean() methods dereference required FKs. Do not
            # invoke them until ordinary field validation has populated them.
            if self._errors:
                return
            if spec.label == 'publishing.page':
                content = dict(self.instance.content_json or {})
                content['editorial'] = {'text':self.cleaned_data.get('editorial_text','')}
                self.instance.content_json = content
            super()._post_clean()
            if not self.errors:
                try:
                    validate_relationships(self.instance)
                except ValidationError as error:
                    self._update_errors(error)

    initial = {'version':version_token(spec,obj,actor)}
    if spec.label == 'publishing.page':
        OperationForm.base_fields['editorial_text'] = forms.CharField(widget=forms.Textarea,required=False,max_length=50000,label='Editorial text',help_text='Original imported content remains preserved separately.')
        initial['editorial_text'] = (obj.content_json or {}).get('editorial',{}).get('text','') if obj else ''
    for name in ('secondary_types','capabilities'):
        if name in names:
            OperationForm.base_fields[name] = forms.CharField(required=False,label=name.replace('_',' ').capitalize(),help_text='Comma-separated values, up to 10 entries of 30 characters each.')
            initial[name] = ', '.join(getattr(obj,name,[]) or []) if obj else ''
    form = OperationForm(instance=obj, data=data, initial=initial)
    for name, field in form.fields.items():
        if isinstance(field,forms.URLField):
            field.widget = RedactedURLInput(attrs={'placeholder':'Enter a public HTTPS URL'})
            if name in original_unsafe_urls:
                field.required = False
                field.help_text = 'The existing URL is withheld because it contains private or unsafe information. Leave blank to keep it, or enter a safe public HTTPS URL to replace it.'
        if isinstance(field,forms.ModelChoiceField):
            field.queryset = field.queryset.order_by('pk')
            field.label_from_instance = object_label
    if 'join_phrase' in form.fields:
        form.fields['join_phrase'].strip = False
    return form


def workflow_links(spec, obj, user):
    links = []
    if spec.workflow == 'users' and user.is_superuser:
        links.append({'title':'Manage users and workspace access','url':reverse('portal:user-list')})
    if spec.workflow == 'review':
        links.append({'title':'Open review queue','url':reverse('portal:review-queue')})
        if obj:
            name = 'portal:review-issue' if spec.label == 'provenance.reviewitem' else 'portal:review-request'
            links.append({'title':'Review this item','url':reverse(name,args=[obj.pk])})
    artist = getattr(obj,'artist',None) if obj else None
    website = obj if spec.label == 'publishing.website' else getattr(obj,'website',None) if obj else None
    if website:
        artist = website.artist
    if artist:
        links.append({'title':'Open artist workspace','url':reverse('portal:artist',args=[artist.pk])})
        if spec.workflow == 'publication':
            links.append({'title':'Review and publish catalog','url':reverse('portal:publish',args=[artist.pk])})
            links.append({'title':'Publication settings','url':reverse('portal:publication-settings',args=[artist.pk])})
    if obj and spec.label == 'artists.artist':
        links.append({'title':'Publication review settings','url':reverse('portal:publication-settings',args=[obj.pk])})
    if obj and spec.label == 'portal.operationremoval':
        target_spec = REGISTRY.get(obj.model_label.replace('.','-'))
        if target_spec:
            target = target_spec.model.objects.filter(pk=obj.object_id).first()
            if target:
                links.append({'title':'Open preserved relationship','url':detail_url(target,user)})
                if obj.restored_at is None:
                    links.append({'title':'Restore relationship','url':reverse('portal:operation-restore',args=[target_spec.key,str(target.pk)])})
    if obj and not source_bound(spec,obj):
        from .resources import RESOURCES
        from .querysets import scoped
        kind = next((name for name,resource in RESOURCES.items() if resource.model is type(obj)),None)
        if kind:
            candidates = Artist.objects.filter(archived_at__isnull=True,management_status='managed').filter(organization_id__in=organization_ids(obj)).order_by('name','pk')
            for workspace in candidates:
                if scoped(user,workspace,kind,write=True).filter(pk=obj.pk).exists():
                    links.append({'title':f'Edit in {workspace.name} workspace','url':reverse('portal:edit',args=[workspace.pk,kind,str(obj.pk)])})
                    links.append({'title':f'Remove from {workspace.name} workspace','url':reverse('portal:delete',args=[workspace.pk,kind,str(obj.pk)])})
                    break
    if spec.workflow == 'upload':
        links.append({'title':'Upload and manage files in an artist workspace','url':reverse('portal:overview')})
    return links


@operator_required
@require_GET
def hub(request):
    from .views import context
    groups = {}
    for spec in REGISTRY.values():
        if spec.superuser and not request.user.is_superuser:
            continue
        group = spec.model._meta.app_label
        groups.setdefault(group,[]).append({'spec':spec,'count':spec.model.objects.count(),'url':reverse('portal:operation-table',args=[spec.key])})
    return render(request,'portal/operations.html',context(request,operation_groups=groups.items()))


@operator_required
@require_GET
def table(request, key):
    from .views import context
    spec = operation(key,request.user)
    rows = queryset(spec)
    query = request.GET.get('q','')[:200].strip()
    if query:
        predicate = Q()
        for name in spec.fields:
            field = spec.model._meta.get_field(name)
            if isinstance(field,(models.CharField,models.TextField)):
                predicate |= Q(**{f'{name}__icontains':query})
            elif field.is_relation:
                related = REGISTRY.get(field.related_model._meta.label_lower.replace('.','-'))
                for related_name in ('name','title','display_name','domain','username','code'):
                    if related and related_name in related.fields:
                        predicate |= Q(**{f'{name}__{related_name}__icontains':query})
        rows = rows.filter(predicate) if predicate else rows.none()
    relation_params = {}
    if request.GET.get('related_model'):
        parent = REGISTRY.get(request.GET.get('related_model'))
        field_name = request.GET.get('related_field','')
        try:
            field = spec.model._meta.get_field(field_name)
            if not parent or not field.is_relation or field.related_model is not parent.model:
                raise Http404
            require_operator(request.user,parent)
            related_id = request.GET.get('related_id','')
            parent_obj = get_object_or_404(parent.model,pk=related_id)
            rows = rows.filter(**{field.name:parent_obj})
            relation_params = {'related_model':parent.key,'related_field':field_name,'related_id':related_id}
        except (FieldDoesNotExist, ValidationError, ValueError):
            raise Http404 from None
    page = Paginator(rows,30).get_page(request.GET.get('page'))
    summaries = [{'label':object_label(row),'url':detail_url(row,request.user),'values':display_fields(row,spec,request.user)[:6],'removed':spec.label in REMOVABLE and is_removed(row)} for row in page]
    can_create = bool(spec.edit_fields) and spec.label not in NO_CREATE
    return render(request,'portal/operations_table.html',context(request,spec=spec,rows=summaries,page_obj=page,query=query,can_create=can_create,workflow_links=workflow_links(spec,None,request.user),relation_params=relation_params,pagination_query=urlencode({'q':query,**relation_params})))


@operator_required
@require_GET
def detail(request,key,object_id):
    from .views import context
    spec = operation(key,request.user)
    if spec.label == 'sessions.session':
        raise Http404
    obj = get_object_or_404(queryset(spec),pk=object_id)
    removed = spec.label in REMOVABLE and is_removed(obj)
    related = []
    for relation in obj._meta.related_objects:
        target = REGISTRY.get(relation.related_model._meta.label_lower.replace('.','-'))
        if not target or (target.superuser and not request.user.is_superuser):
            continue
        count = target.model.objects.filter(**{relation.field.name:obj}).count()
        if count:
            url = reverse('portal:operation-table',args=[target.key]) + '?' + urlencode({'related_model':spec.key,'related_id':str(obj.pk),'related_field':relation.field.name})
            related.append({'title':target.title,'count':count,'url':url})
    return render(request,'portal/operations_detail.html',context(request,spec=spec,obj=obj,object_label=object_label(obj),fields=display_fields(obj,spec,request.user),related=related,workflow_links=workflow_links(spec,obj,request.user),can_edit_record=bool(spec.edit_fields and not source_bound(spec,obj) and not removed),source_bound=source_bound(spec,obj),removed=removed,can_remove=bool(spec.label in REMOVABLE and not source_bound(spec,obj) and not removed),can_restore=bool(spec.label in REMOVABLE and not source_bound(spec,obj) and removed),can_archive=bool(spec.edit_fields and hasattr(obj,'archived_at') and not obj.archived_at),can_revoke=spec.label in REVOKABLE,version=version_token(spec,obj,request.user)))


def locked_actor(actor,spec,obj=None,data=None):
    # Catalog/user operations lock artists before users. Match that order.
    ids = organization_ids(obj)
    if data and data.get('organization'):
        ids.add(data['organization'])
    if data and data.get('artist'):
        ids |= set(Artist.objects.filter(pk=data['artist']).values_list('organization_id',flat=True))
    if obj and spec.label == 'catalog.asset' and obj.organization_id is None:
        # Public shared assets may affect multiple organizations. Serialize
        # their withdrawal before taking asset/site locks, as publication does.
        list(Artist.objects.select_for_update().order_by('pk'))
    elif ids:
        list(Artist.objects.select_for_update().filter(organization_id__in=ids).order_by('pk'))
    user_ids = {actor.pk}
    if obj and getattr(obj,'user_id',None):
        user_ids.add(obj.user_id)
    locked_users = {row.pk:row for row in get_user_model().objects.select_for_update().filter(pk__in=user_ids).order_by('pk')}
    current = locked_users.get(actor.pk)
    if not current:
        raise PermissionDenied('Staff access was removed.')
    require_operator(current,spec)
    return current


def audit(actor,spec,obj,action,changed_fields):
    LogEntry.objects.create(user=actor,content_type=ContentType.objects.get_for_model(spec.model),object_id=str(obj.pk),object_repr=object_label(obj)[:200],action_flag=ADDITION if action=='create' else DELETION if action=='revoke' else CHANGE,change_message=f'Portal operations: {action}. Fields: '+', '.join(changed_fields))
    ids = organization_ids(obj)
    for organization_id in ids:
        AuditEvent.objects.create(organization_id=organization_id,user=actor,action=f'portal.operations.{action}',target_label=spec.label,change_json={'object_id':str(obj.pk),'changed_fields':list(changed_fields)})


def revoke_affected(obj):
    from publishing.projection import revoke_for_resource
    revoke_for_resource(obj)
    # Relationship edits and page metadata can affect an already approved
    # artist/recording/release/site even though the row itself is not selected.
    for name in ('artist','recording','release','work','asset','website','fanlink_page','page','from_recording','to_recording'):
        parent = getattr(obj,name,None)
        if isinstance(parent,models.Model):
            revoke_for_resource(parent)
            if getattr(parent,'website_id',None):
                revoke_for_resource(parent.website)


@transaction.atomic
def save_operation(actor,key,data,object_id=None):
    spec = operation(key,actor)
    if not spec.edit_fields or (not object_id and spec.label in NO_CREATE):
        raise PermissionDenied('Use the dedicated workflow for this record.')
    original = get_object_or_404(spec.model,pk=object_id) if object_id else None
    current_actor = locked_actor(actor,spec,original,data)
    obj = get_object_or_404(spec.model.objects.select_for_update(),pk=object_id) if object_id else None
    if obj and getattr(obj,'archived_at',None):
        raise PermissionDenied('Archived records cannot be edited.')
    require_mutable_record(spec,obj)
    if obj and spec.label in REMOVABLE and is_removed(obj):
        raise PermissionDenied('Restore this preserved relationship before editing it.')
    check_version(spec,obj,current_actor,data.get('version'))
    previous_asset_rights = (obj.rights_approved,obj.visibility) if obj and spec.label == 'catalog.asset' else None
    form = make_operation_form(spec,current_actor,obj,data)
    if not form.is_valid():
        return None,form
    saved = form.save(commit=False)
    if obj and hasattr(saved,'revision'):
        saved.revision += 1
    if spec.label == 'catalog.asset' and {'rights_basis','visibility'} & set(form.changed_data) and 'rights_approved' not in form.changed_data:
        saved.rights_approved = False
    validate_relationships(saved)
    saved.full_clean()
    saved.save()
    if previous_asset_rights and ((previous_asset_rights[0] and not saved.rights_approved) or (previous_asset_rights[1]=='public' and saved.visibility!='public')):
        revoke_affected(saved)
    audit(current_actor,spec,saved,'update' if obj else 'create',[name for name in form.changed_data if name!='version'])
    return saved,form


@operator_required
@require_http_methods(['GET','POST'])
def edit(request,key,object_id=None):
    from .views import context
    spec = operation(key,request.user)
    if not spec.edit_fields or (not object_id and spec.label in NO_CREATE):
        raise PermissionDenied('Use the dedicated workflow for this record.')
    obj = get_object_or_404(spec.model,pk=object_id) if object_id else None
    require_mutable_record(spec,obj)
    if obj and spec.label in REMOVABLE and is_removed(obj):
        raise PermissionDenied('Restore this preserved relationship before editing it.')
    form = make_operation_form(spec,request.user,obj)
    status = 200
    if request.method == 'POST':
        try:
            saved,form = save_operation(request.user,key,request.POST,object_id)
            if saved:
                messages.success(request,'Record saved.')
                return redirect('portal:operation-detail',key=key,object_id=str(saved.pk))
            status = 400
        except (ValidationError,IntegrityError) as error:
            form = make_operation_form(spec,request.user,obj,request.POST)
            form.is_valid()
            form.add_error(None,'; '.join(error.messages) if isinstance(error,ValidationError) else 'This record conflicts with another saved record. Review the form.')
            status = 409 if isinstance(error,StaleEdit) else 400
    return render(request,'portal/operations_form.html',context(request,spec=spec,obj=obj,form=form),status=status)


@transaction.atomic
def retire_operation(actor,key,object_id,token,revoke=False):
    spec = operation(key,actor)
    original = get_object_or_404(spec.model,pk=object_id)
    current_actor = locked_actor(actor,spec,original)
    obj = get_object_or_404(spec.model.objects.select_for_update(),pk=object_id)
    require_mutable_record(spec,obj)
    check_version(spec,obj,current_actor,token)
    if revoke:
        if spec.label not in REVOKABLE:
            raise PermissionDenied('This record cannot be revoked.')
        if spec.label == 'artists.artistresourcegrant':
            list(ArtistResourceGrant.objects.select_for_update().filter(artist_id=obj.artist_id).order_by('pk'))
        audit(current_actor,spec,obj,'revoke',[])
        obj.delete()
    else:
        if not spec.edit_fields or not hasattr(obj,'archived_at'):
            raise PermissionDenied('This record is preserved by its owning workflow.')
        revoke_affected(obj)
        obj.archived_at = timezone.now()
        obj.revision += 1
        obj.save()
        audit(current_actor,spec,obj,'archive',[])
    return spec


@operator_required
@require_http_methods(['GET','POST'])
def archive(request,key,object_id,revoke=False):
    from .views import context
    spec = operation(key,request.user)
    obj = get_object_or_404(spec.model,pk=object_id)
    if (revoke and spec.label not in REVOKABLE) or (not revoke and (not spec.edit_fields or not hasattr(obj,'archived_at'))):
        raise PermissionDenied('Use the dedicated workflow for this record.')
    error = ''
    if request.method == 'POST':
        try:
            retire_operation(request.user,key,object_id,request.POST.get('version'),revoke)
            messages.success(request,'Access revoked.' if revoke else 'Record archived.')
            return redirect('portal:operation-table',key=key)
        except StaleEdit as exc:
            error = exc.messages[0]
    return render(request,'portal/operations_confirm.html',context(request,spec=spec,obj=obj,object_label=object_label(obj),version=version_token(spec,obj,request.user),revoke=revoke,error=error),status=409 if error else 200)


def revoke(request,key,object_id):
    return archive(request,key,object_id,revoke=True)


@transaction.atomic
def change_removal(actor,key,object_id,token,*,restore=False):
    """Preserve a global relationship without inventing artist ownership."""
    from .models import OperationRemoval
    spec = operation(key,actor)
    if spec.label not in REMOVABLE:
        raise PermissionDenied('This record is preserved by its owning workflow.')
    original = get_object_or_404(spec.model,pk=object_id)
    current_actor = locked_actor(actor,spec,original)
    obj = get_object_or_404(spec.model.objects.select_for_update(),pk=object_id)
    require_mutable_record(spec,obj)
    check_version(spec,obj,current_actor,token)
    marker = OperationRemoval.objects.select_for_update().filter(model_label=spec.label,object_id=str(obj.pk)).first()
    if restore:
        workspace_marker = workspace_removal(obj)
        if not is_removed(obj):
            raise StaleEdit('This relationship is already active. Reload its record.')
        validate_relationships(obj)
        obj.full_clean()
        if marker is None:
            ids = organization_ids(obj)
            marker = OperationRemoval.objects.create(model_label=spec.label,object_id=str(obj.pk),organization_id=next(iter(ids)) if len(ids)==1 else None,removed_by_id=workspace_marker.user_id,removed_at=workspace_marker.created_at,restored_by=current_actor,restored_at=timezone.now())
        else:
            marker.restored_by = current_actor
            marker.restored_at = timezone.now()
            marker.save(update_fields=['restored_by','restored_at','updated_at'])
        # Existing workspace removals also use EditorialChange. A restoration
        # must supersede their last delete marker for every existing workspace.
        from .models import EditorialChange
        artist_ids = set(EditorialChange.objects.filter(model_label=spec.label,object_id=str(obj.pk),action__in=['delete','restore']).values_list('artist_id',flat=True))
        for workspace in Artist.objects.filter(pk__in=artist_ids).order_by('pk'):
            EditorialChange.objects.create(organization=workspace.organization,artist=workspace,user=current_actor,model_label=spec.label,object_id=str(obj.pk),action='restore',changed_fields=[])
        audit(current_actor,spec,obj,'restore',[])
    else:
        if marker and marker.restored_at is None:
            raise StaleEdit('This relationship has already been removed. Reload its record.')
        revoke_affected(obj)
        ids = organization_ids(obj)
        values = {'organization_id':next(iter(ids)) if len(ids)==1 else None,'removed_by':current_actor,'removed_at':timezone.now(),'restored_by':None,'restored_at':None}
        if marker:
            for name,value in values.items():
                setattr(marker,name,value)
            marker.save()
        else:
            OperationRemoval.objects.create(model_label=spec.label,object_id=str(obj.pk),**values)
        audit(current_actor,spec,obj,'remove',[])
    return obj


@operator_required
@require_http_methods(['GET','POST'])
def remove_relationship(request,key,object_id,restore=False):
    from .views import context
    spec = operation(key,request.user)
    if spec.label not in REMOVABLE:
        raise PermissionDenied('This record is preserved by its owning workflow.')
    obj = get_object_or_404(spec.model,pk=object_id)
    require_mutable_record(spec,obj)
    error = ''
    if request.method == 'POST':
        try:
            change_removal(request.user,key,object_id,request.POST.get('version'),restore=restore)
            messages.success(request,'Relationship restored.' if restore else 'Relationship removed and preserved in history.')
            return redirect('portal:operation-detail',key=key,object_id=object_id)
        except (StaleEdit,ValidationError) as exc:
            error = '; '.join(exc.messages)
    return render(request,'portal/operations_removal.html',context(request,spec=spec,obj=obj,object_label=object_label(obj),version=version_token(spec,obj,request.user),restore=restore,error=error),status=409 if error else 200)


def restore_relationship(request,key,object_id):
    return remove_relationship(request,key,object_id,restore=True)
