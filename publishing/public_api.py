"""Anonymous GET views serve only a validated active projection."""
from urllib.parse import urlencode
from functools import wraps
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from catalog.models import Recording, Release, Asset, Label, ReleaseGroup
from rights.models import MusicalWork
from artists.models import ArtistResourceGrant
from publishing.models import FanlinkPage, WebsiteApprovedState
from .projection import validate_payload, digest, normalize_selection, public_id, artwork_urls


def active_state(slug):
    try:
        state = WebsiteApprovedState.objects.select_related('active_revision','website__artist').get(approved_slug=slug,active_revision__isnull=False)
    except WebsiteApprovedState.DoesNotExist: raise Http404 from None
    if state.website.archived_at or state.website.artist.archived_at: raise Http404
    revision = state.active_revision
    try:
        selection=normalize_selection(revision.selection, schema_version=revision.schema_version)
        validate_payload(revision.payload, schema_version=revision.schema_version)
    except (ValidationError,KeyError,TypeError,ValueError): raise Http404 from None
    if revision.website_id != state.website_id or revision.payload['artist']['slug'] != slug: raise Http404
    # Fail closed even if an operator bypassed the mutation services.
    for key, model in [('recordings',Recording),('releases',Release),('fanlinks',FanlinkPage),('artwork',Asset), *([('lyrics',Asset),('works',MusicalWork)] if revision.schema_version == 2 else [])]:
        ids = selection[key]
        if not isinstance(ids,list): raise Http404
        rows = model.objects.filter(pk__in=ids, archived_at__isnull=True)
        if key=='fanlinks': rows=rows.filter(website_id=state.website_id)
        else:
            rows=rows.filter(organization_id=state.website.organization_id)
            field='asset' if key in ('artwork','lyrics') else key[:-1]
            granted=ArtistResourceGrant.objects.filter(artist_id=state.website.artist_id,**{f'{field}_id__in':ids}).values_list(f'{field}_id',flat=True)
            if {str(value) for value in granted} != set(ids): raise Http404
        if key in ('artwork','lyrics'): rows=rows.filter(visibility='public',rights_approved=True).exclude(rights_basis='')
        if rows.count() != len(ids): raise Http404
    if revision.schema_version == 2:
        objects = [*revision.payload['recordings'], *revision.payload['releases']]
        required_labels = {label['id'] for item in objects for label in item['labels']}
        required_groups = {item['release_group']['id'] for item in revision.payload['releases'] if item['release_group']}
        for kind, model, required in [('label', Label, required_labels), ('release_group', ReleaseGroup, required_groups)]:
            granted = model.objects.filter(organization_id=state.website.organization_id, archived_at__isnull=True,
                                           artist_grants__artist_id=state.website.artist_id).values_list('pk', flat=True)
            if not required <= {public_id(kind, pk) for pk in granted}: raise Http404
    if digest(revision.payload) != revision.checksum: raise Http404
    try: validate_payload(revision.payload, schema_version=revision.schema_version)
    except (ValidationError,KeyError,TypeError,ValueError): raise Http404 from None
    return state


def response(data):
    result = Response(data)
    result['Cache-Control'] = 'no-store'
    result['X-Content-Type-Options'] = 'nosniff'
    return result


def no_store(view):
    @wraps(view)
    def wrapped(*args,**kwargs):
        result=view(*args,**kwargs)
        result['Cache-Control']='no-store'
        result['X-Content-Type-Options']='nosniff'
        return result
    return wrapped


@no_store
@api_view(['GET','HEAD'])
@authentication_classes([])
@permission_classes([AllowAny])
def artist_detail(request, slug):
    revision = active_state(slug).active_revision
    return response({'schema_version':revision.schema_version,'content_version':revision.content_version,'artist':revision.payload['artist']})


@no_store
@api_view(['GET','HEAD'])
@authentication_classes([])
@permission_classes([AllowAny])
def collection(request, slug, kind, resource_id=None):
    revision = active_state(slug).active_revision
    envelope = {'schema_version':revision.schema_version,'content_version':revision.content_version,'artist':revision.payload['artist']}
    rows = revision.payload[kind]
    if resource_id is not None:
        key = 'slug' if kind=='fanlinks' else 'id'
        item = next((item for item in rows if item[key]==str(resource_id)),None)
        if item is None: raise Http404
        return response({**envelope,kind[:-1]:item})
    if set(request.query_params)-{'page','page_size','q','type'}:
        return _bad_request()
    try:
        page = int(request.query_params.get('page','1')); size = int(request.query_params.get('page_size','50'))
        if page < 1 or size < 1 or size > 100: raise ValueError
        query = request.query_params.get('q','')
        resource_type = request.query_params.get('type','')
        if len(query)>200 or len(resource_type)>30: raise ValueError
    except ValueError: return _bad_request()
    if query: rows = [row for row in rows if query.casefold() in row['title'].casefold()]
    if resource_type:
        field = {'recordings':'recording_type','releases':'release_type','fanlinks':'target_type'}[kind]
        rows = [row for row in rows if row[field]==resource_type]
    rows = sorted(rows,key=lambda row:(row['title'].casefold(),row['id']))
    start=(page-1)*size
    next_url = None
    if start+size<len(rows):
        parameters={'page':page+1,'page_size':size}
        if query: parameters['q']=query
        if resource_type: parameters['type']=resource_type
        next_url=f'/api/public/v1/artists/{slug}/{kind}/?'+urlencode(parameters)
    return response({**envelope,'results':rows[start:start+size],'next':next_url})


def _bad_request():
    result=response({'detail':'Invalid public query parameters.'}); result.status_code=400; return result


@no_store
@api_view(['GET','HEAD'])
@authentication_classes([])
@permission_classes([AllowAny])
def artwork(request,slug,resource_id):
    state=active_state(slug)
    reference=state.active_revision.artwork_versions.select_related('version__asset').filter(public_id=resource_id).first()
    if reference is None: raise Http404
    version=reference.version; asset=version.asset
    expected=public_id('artwork',version.pk)
    approved_url=f'/api/public/v1/artists/{slug}/artwork/{expected}/'
    if str(resource_id)!=expected or str(asset.pk) not in state.active_revision.selection['artwork'] or approved_url not in artwork_urls(state.active_revision.payload): raise Http404
    if asset.archived_at or asset.visibility!='public' or not asset.rights_approved or not asset.rights_basis or version.mime_type not in ('image/jpeg','image/png','image/webp') or not version.preview_width or not version.preview_height: raise Http404
    try: handle=version.preview_file.open('rb')
    except (FileNotFoundError,OSError,ValueError): raise Http404 from None
    result=FileResponse(handle,content_type='image/webp')
    result['Cache-Control']='no-store'; result['X-Content-Type-Options']='nosniff'
    result['Content-Security-Policy']="default-src 'none'; sandbox"
    result['Content-Disposition']='inline; filename="artwork.webp"'
    return result
