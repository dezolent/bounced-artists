"""Explicit administrator-only system privileges and credential operations."""
import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm, UsernameField
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from artists.models import ArtistMembership
from .services import StaleEdit
from .user_management import administrator_required, require_administrator


User = get_user_model()
ROLES = {'artist': (False, False), 'staff': (True, False), 'administrator': (True, True)}
TOKEN_SALT = 'portal-system-account'


class SystemAccessForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    username = UsernameField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False, max_length=254)
    role = forms.ChoiceField(choices=[('artist', 'Artist account'), ('staff', 'Staff operator'), ('administrator', 'Administrator')])
    is_active = forms.BooleanField(label='Active account', required=False)

    def __init__(self, target, *args, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)
        self.fields['username'].validators.extend(User._meta.get_field('username').validators)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exclude(pk=self.target.pk).exists():
            raise ValidationError('A user with that username already exists.')
        return username


class ResetAccountPasswordForm(SetPasswordForm):
    version = forms.CharField(widget=forms.HiddenInput)

    @sensitive_variables()
    def clean_new_password1(self):
        value = self.cleaned_data['new_password1']
        if len(value) < 12:
            raise ValidationError('Use at least 12 characters.')
        return value


def _digest(user):
    # The browser receives a digest, never a password hash or other credential.
    state = {name: getattr(user, name) for name in ('pk', 'username', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_superuser', 'password')}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _snapshot(actor, target, action):
    return {'actor': actor.pk, 'actor_revision': _digest(actor), 'target': target.pk,
            'target_revision': _digest(target), 'action': action}


def _form(actor, target, action, data=None):
    initial = {'version': signing.dumps(_snapshot(actor, target, action), salt=TOKEN_SALT)}
    if action == 'password':
        return ResetAccountPasswordForm(target, data=data, initial=initial)
    initial.update({name: getattr(target, name) for name in ('username', 'first_name', 'last_name', 'email', 'is_active')})
    initial.update(role='administrator' if target.is_superuser else 'staff' if target.is_staff else 'artist')
    return SystemAccessForm(target, data=data, initial=initial)


def _validate_snapshot(actor, target, action, data):
    try:
        actual = signing.loads(data.get('version', ''), salt=TOKEN_SALT, max_age=86400)
    except (signing.BadSignature, TypeError):
        raise StaleEdit('Reload this account form before saving. Its snapshot is invalid or expired.') from None
    if actual != _snapshot(actor, target, action):
        raise StaleEdit('The account or your administrator access changed. Reload to review the latest account state.')


def _audit(actor, target, message):
    LogEntry.objects.create(user=actor, content_type=ContentType.objects.get_for_model(User),
                            object_id=str(target.pk), object_repr=target.username,
                            action_flag=CHANGE, change_message=message)


@transaction.atomic
@sensitive_variables('data', 'form')
def save_system_account(actor, target_id, action, data):
    # Every system-account mutation locks all active administrators as well as
    # its actor and target, in one shared PK order. Concurrent demotions cannot
    # each observe the other administrator as the remaining active one.
    locked = {row.pk: row for row in User.objects.select_for_update().filter(
        Q(is_active=True, is_staff=True, is_superuser=True) | Q(pk__in=[actor.pk, target_id])
    ).order_by('pk')}
    current_actor = locked.get(actor.pk)
    if current_actor is None:
        raise PermissionDenied('Administrator access was removed.')
    require_administrator(current_actor)
    target = locked.get(target_id)
    if target is None:
        from django.http import Http404
        raise Http404
    _validate_snapshot(current_actor, target, action, data)
    form = _form(current_actor, target, action, data)
    valid = form.is_valid()
    if set(data) - (set(form.fields) | {'csrfmiddlewaretoken'}):
        form.add_error(None, 'Unexpected fields in this request. Use only the fields shown.')
        valid = False
    if not valid:
        return None, form
    if action == 'password':
        form.save(commit=False)
        target.save(update_fields=['password'])
        _audit(current_actor, target, 'Reset account password through the portal.')
    else:
        staff, superuser = ROLES[form.cleaned_data['role']]
        active = form.cleaned_data['is_active']
        loses_administrator = target.is_active and target.is_staff and target.is_superuser and not (active and staff and superuser)
        if loses_administrator:
            if not any(row.pk != target.pk and row.is_active and row.is_staff and row.is_superuser for row in locked.values()):
                form.add_error(None, 'The final active administrator cannot be deactivated or demoted.')
                return None, form
            if target.pk == current_actor.pk:
                form.add_error(None, 'You cannot deactivate or demote your own administrator account. Ask another administrator to make this change.')
                return None, form
        previous = {'is_active': target.is_active, 'is_staff': target.is_staff, 'is_superuser': target.is_superuser}
        target.is_active, target.is_staff, target.is_superuser = active, staff, superuser
        profile_fields = ('username', 'first_name', 'last_name', 'email')
        for name in profile_fields:
            setattr(target, name, form.cleaned_data[name])
        target.save(update_fields=['is_active', 'is_staff', 'is_superuser', *profile_fields])
        _audit(current_actor, target, json.dumps({'operation': 'portal.system_access', 'previous': previous,
            'current': {'is_active': active, 'is_staff': staff, 'is_superuser': superuser}, 'profile_fields': list(profile_fields)}, sort_keys=True))
    return target, form


@sensitive_variables('form')
def _account_view(request, user_id, action):
    from .views import context
    target = get_object_or_404(User, pk=user_id)
    form = _form(request.user, target, action, request.POST if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        try:
            saved, form = save_system_account(request.user, user_id, action, request.POST)
            if saved:
                if action == 'password' and saved.pk == request.user.pk:
                    update_session_auth_hash(request, saved)
                messages.success(request, 'Account password reset. Share the new password directly; no email was sent.' if action == 'password' else 'System access updated.')
                return redirect('portal:user-list')
            status = 400
        except StaleEdit as error:
            form.is_valid()
            form.add_error(None, error)
            status = 409
        except IntegrityError:
            form.is_valid()
            form.add_error(None, 'The account could not be saved. Reload and check its username before trying again.')
            status = 400
    has_workspaces = ArtistMembership.objects.filter(user=target, artist__archived_at__isnull=True, artist__management_status='managed').exists()
    template = 'portal/system_account_password.html' if action == 'password' else 'portal/system_account.html'
    return render(request, template, context(request, account=target, form=form, has_workspaces=has_workspaces), status=status)


@administrator_required
@require_http_methods(['GET', 'POST'])
def system_account(request, user_id):
    return _account_view(request, user_id, 'access')


@administrator_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('new_password1', 'new_password2')
def reset_account_password(request, user_id):
    return _account_view(request, user_id, 'password')
