"""Workspace selection is presentation context, never a source of extra access."""
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.urls import reverse
from .access import artist_for,artists_for


SESSION_KEY='portal_artist_id'


def selected_workspace(request):
    available=artists_for(request.user)
    if request.GET.get('artist'):
        return artist_for(request.user,request.GET['artist'])
    remembered=request.session.get(SESSION_KEY)
    if remembered:
        try:
            selected=available.filter(pk=remembered).first()
        except (ValidationError,ValueError,TypeError):
            selected=None
        if selected:
            return selected
        request.session.pop(SESSION_KEY,None)
    return available.filter(bootstrap_key='dezolent').first() or available.first()


def switch_url(request,artist):
    """Keep collection sections/filters; record IDs never cross artist contexts."""
    match=request.resolver_match
    name=match.url_name if match else 'overview'
    kwargs=match.kwargs if match else {}
    params={}
    if name=='overview':
        path=reverse('portal:overview')
        params['artist']=str(artist.pk)
    elif name in ('catalog','recording','release'):
        path=reverse('portal:catalog',args=[artist.pk])
        if name=='release' or name=='catalog' and request.GET.get('kind')=='releases':
            params['kind']='releases'
        if name=='catalog':
            for key in ('q','status'):
                value=request.GET.get(key,'')[:200]
                if value and (key!='status' or value in ('draft','review','approved')):
                    params[key]=value
            if request.GET.get('song_type') in ('solo','collaborations','remixes') and request.GET.get('kind')!='releases':
                params['song_type']=request.GET['song_type']
            if request.GET.get('genre'):
                params['genre']=request.GET['genre'][:100]
    elif name in ('resources','detail','edit','create','delete'):
        kind=kwargs.get('kind')
        path=reverse('portal:artist',args=[artist.pk]) if kind=='artists' else reverse('portal:resources',args=[artist.pk,kind])
        if name=='resources' and request.GET.get('q'):
            params['q']=request.GET['q'][:200]
        if name=='resources' and kind=='assets':
            from catalog.models import Asset
            from catalog.brand_library import LIBRARY_CATEGORIES
            if request.GET.get('library')=='shared':
                params['library']='shared'
            if request.GET.get('sort') in ('name','type','-type','newest','visibility','-visibility'):
                params['sort']=request.GET['sort']
            if request.GET.get('visibility') in dict(Asset._meta.get_field('visibility').choices):
                params['visibility']=request.GET['visibility']
            if request.GET.get('category') in dict(list(Asset.Category.choices)+LIBRARY_CATEGORIES):
                params['category']=request.GET['category']
    else:
        path=reverse('portal:artist',args=[artist.pk])
    return path+('?' + urlencode(params) if params else '')


def workspace_context(request,artist):
    if artist and request.session.get(SESSION_KEY)!=str(artist.pk):
        request.session[SESSION_KEY]=str(artist.pk)
    available=list(artists_for(request.user))
    for workspace in available:
        workspace.workspace_url=switch_url(request,workspace)
    return {'artists':available,'overview_url':reverse('portal:overview')+(f'?artist={artist.pk}' if artist else '')}
