"""Superuser-only artist account onboarding and explicit workspace membership."""
from functools import wraps
import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION,CHANGE,LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm,UsernameField
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError,transaction
from django.db.models import Prefetch,Q
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET,require_http_methods

from artists.models import Artist,ArtistMembership
from provenance.models import AuditEvent
from .services import StaleEdit


User=get_user_model()
PROFILE_FIELDS=('username','first_name','last_name','email','is_active')
TOKEN_SALT='portal-user-access'


def require_administrator(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.is_superuser:
        raise PermissionDenied('Only a system administrator can manage users and workspace access.')


def administrator_required(view):
    @login_required
    @wraps(view)
    def guarded(request,*args,**kwargs):
        require_administrator(request.user)
        return view(request,*args,**kwargs)
    return guarded


class CreateAccountForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model=User
        fields=PROFILE_FIELDS


class UpdateAccountForm(forms.ModelForm):
    class Meta:
        model=User
        fields=PROFILE_FIELDS
        field_classes={'username':UsernameField}

    def clean_username(self):
        username=self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise ValidationError('A user with that username already exists.')
        return username


class AccountEditor:
    def __init__(self,actor,target=None,data=None):
        self.actor,self.target,self.data=actor,target,data
        self.workspaces=list(Artist.objects.filter(archived_at__isnull=True,management_status=Artist.ManagementStatus.MANAGED).select_related('organization').order_by('organization__name','name','pk'))
        self.memberships=list(ArtistMembership.objects.filter(user=target).select_related('artist__organization').order_by('artist_id')) if target else []
        self.account_form=(UpdateAccountForm if target else CreateAccountForm)(data=data,instance=target)
        role_by_artist={row.artist_id:row.role for row in self.memberships}
        fields={'version':forms.CharField(widget=forms.HiddenInput)}
        initial={'version':signing.dumps(self.snapshot(),salt=TOKEN_SALT)}
        for artist in self.workspaces:
            name=f'workspace_{artist.pk}'
            fields[name]=forms.ChoiceField(label=artist.name,choices=[('none','No access'),*ArtistMembership.Role.choices])
            initial[name]=role_by_artist.get(artist.pk,'none')
        role_form=type('WorkspaceRolesForm',(forms.Form,),fields)
        self.workspace_form=role_form(data=data,initial=initial)
        self.workspace_rows=[{'artist':artist,'field':self.workspace_form[f'workspace_{artist.pk}']} for artist in self.workspaces]
        ids={artist.pk for artist in self.workspaces}
        self.historical_memberships=[row for row in self.memberships if row.artist_id not in ids]

    def snapshot(self):
        # Only the digest is signed/sent to the browser. Credential revisions help
        # detect concurrent password changes without exposing the stored hash.
        profile={name:getattr(self.target,name) for name in (*PROFILE_FIELDS,'is_staff','is_superuser','password')} if self.target else None
        value={'profile':profile,'workspaces':[str(artist.pk) for artist in self.workspaces],
               'memberships':[(str(row.artist_id),row.role,str(row.updated_at)) for row in self.memberships]}
        checksum=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        return {'actor':self.actor.pk,'target':self.target.pk if self.target else None,'checksum':checksum}

    def validate_version(self):
        try:
            actual=signing.loads(self.data.get('version',''),salt=TOKEN_SALT,max_age=86400)
        except (signing.BadSignature,TypeError):
            raise StaleEdit('Reload this user form before saving. Its access snapshot is invalid or expired.') from None
        if actual!=self.snapshot():
            raise StaleEdit('This account, its workspaces or its roles changed since you opened the form. Reload to review the latest access.')

    def is_valid(self):
        account_valid=self.account_form.is_valid()
        roles_valid=self.workspace_form.is_valid()
        allowed=set(self.account_form.fields)|set(self.workspace_form.fields)|{'csrfmiddlewaretoken'}
        if set(self.data)-allowed:
            self.account_form.add_error(None,'Unexpected fields in this request. Use only the account and workspace fields shown.')
            return False
        return account_valid and roles_valid


@transaction.atomic
def save_account(actor,target_id,data):
    # Match catalog writes' lock order: artist, user, membership. Role changes
    # cannot race the permission checks in an in-flight artist edit.
    list(Artist.objects.select_for_update().filter(archived_at__isnull=True,management_status=Artist.ManagementStatus.MANAGED).order_by('pk'))
    locked={row.pk:row for row in User.objects.select_for_update().filter(pk__in=[actor.pk,*([target_id] if target_id else [])]).order_by('pk')}
    current_actor=locked.get(actor.pk)
    if not current_actor:
        raise PermissionDenied('Administrator access was removed.')
    require_administrator(current_actor)
    target=locked.get(target_id) if target_id else None
    if target_id:
        if target is None or target.is_staff or target.is_superuser:
            from django.http import Http404
            raise Http404
        list(ArtistMembership.objects.select_for_update().filter(user=target).order_by('artist_id'))
    editor=AccountEditor(current_actor,target,data)
    editor.validate_version()
    if not editor.is_valid():
        return None,editor
    user=editor.account_form.save(commit=False)
    # New accounts are artist accounts; platform privileges are managed separately.
    if target is None:
        user.is_staff=False
        user.is_superuser=False
    user.save()
    current={row.artist_id:row for row in editor.memberships}
    for artist in editor.workspaces:
        desired=editor.workspace_form.cleaned_data[f'workspace_{artist.pk}']
        existing=current.get(artist.pk)
        previous=existing.role if existing else 'none'
        if desired==previous:
            continue
        if desired=='none':
            existing.delete()
        elif existing:
            existing.role=desired
            existing.save(update_fields=['role','updated_at'])
        else:
            ArtistMembership.objects.create(user=user,artist=artist,role=desired)
        AuditEvent.objects.create(organization=artist.organization,user=current_actor,action='portal.workspace_access',
                                  target_label='Artist workspace membership',change_json={'account_id':user.pk,'artist_id':str(artist.pk),'previous_role':previous,'role':desired})
    LogEntry.objects.create(user=current_actor,content_type=ContentType.objects.get_for_model(User),object_id=str(user.pk),
                            object_repr=user.username,action_flag=CHANGE if target else ADDITION,
                            change_message='Updated artist account and workspace roles.' if target else 'Created artist account and assigned workspace roles.')
    return user,editor


@administrator_required
@require_GET
def user_list(request):
    from .views import context
    query=request.GET.get('q','')[:200]
    rows=User.objects.only('pk','username','first_name','last_name','email','is_active','is_staff','is_superuser').order_by(Lower('username'),'pk')
    if query:
        rows=rows.filter(Q(username__icontains=query)|Q(first_name__icontains=query)|Q(last_name__icontains=query)|Q(email__icontains=query)|Q(artist_memberships__artist__name__icontains=query)).distinct()
    memberships=ArtistMembership.objects.select_related('artist__organization').order_by('artist__name','pk')
    rows=rows.prefetch_related(Prefetch('artist_memberships',queryset=memberships,to_attr='workspace_memberships'))
    page=Paginator(rows,30).get_page(request.GET.get('page'))
    return render(request,'portal/user_list.html',context(request,accounts=page,page_obj=page,query=query))


@administrator_required
@require_http_methods(['GET','POST'])
@sensitive_post_parameters('password1','password2')
def user_edit(request,user_id=None):
    from .views import context
    target=get_object_or_404(User,pk=user_id,is_staff=False,is_superuser=False) if user_id else None
    editor=AccountEditor(request.user,target,request.POST if request.method=='POST' else None)
    status=200
    if request.method=='POST':
        try:
            user,editor=save_account(request.user,user_id,request.POST)
            if user:
                messages.success(request,'User created and workspace access saved.' if target is None else 'User and workspace access updated.')
                if target is None:
                    return redirect('portal:user-list')
                return redirect('portal:user-edit',user_id=user.pk)
            status=400
        except (ValidationError,IntegrityError) as error:
            # The transaction has already rolled back, including account creation.
            editor.account_form.is_valid()
            editor.account_form.add_error(None,error.messages if isinstance(error,ValidationError) else 'The account or workspace access could not be saved. Reload and try again.')
            status=409 if isinstance(error,StaleEdit) else 400
    return render(request,'portal/user_form.html',context(request,account=target,account_form=editor.account_form,
                  workspace_form=editor.workspace_form,workspace_rows=editor.workspace_rows,
                  historical_memberships=editor.historical_memberships,title='Edit user' if target else 'Add user'),status=status)
