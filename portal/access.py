from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404
from artists.models import Artist, ArtistResourceGrant


GRANT_KINDS = {'recording', 'release', 'asset', 'work', 'party', 'event'}


def artists_for(user):
    rows = Artist.objects.filter(archived_at__isnull=True)
    if not user.is_authenticated or not user.is_active:
        return rows.none()
    return rows if user.is_staff else rows.filter(memberships__user=user).distinct()


def artist_for(user, artist_id):
    return get_object_or_404(artists_for(user), pk=artist_id)


def can_edit(user, artist):
    return user.is_active and (user.is_staff or artist.memberships.filter(user=user, role__in=['owner', 'editor']).exists())


def require_editor(user, artist):
    if not can_edit(user, artist):
        raise PermissionDenied('Your role has read-only access.')


def assigned(user, artist, model, kind, *, write=False,include_archived=False):
    rows = model.objects.filter(organization=artist.organization)
    if hasattr(model, 'archived_at') and not include_archived:
        rows = rows.filter(archived_at__isnull=True)
    if user.is_staff:
        return rows
    grants = ArtistResourceGrant.objects.filter(artist=artist)
    if write:
        grants = grants.filter(capability='manage')
    return rows.filter(pk__in=grants.values(f'{kind}_id'))


def grant_created(artist, obj):
    kind = {'musicalwork': 'work'}.get(obj._meta.model_name, obj._meta.model_name)
    if kind in GRANT_KINDS:
        ArtistResourceGrant.objects.get_or_create(artist=artist, **{kind: obj}, defaults={'capability': 'manage'})
