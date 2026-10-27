import hashlib
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import redirect, render
from rest_framework.generics import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from artists.models import Artist
from catalog.models import Asset, AssetVersion, RecordingAsset, ReleaseAsset
from catalog.brand_library import brand_logo,library_rows,LIBRARY_CATEGORIES
from .asset_display import asset_icon,text_preview
from .access import artist_for, can_edit, require_editor
from .forms import make_form, stamp
from .models import LoginThrottle, EditorialChange, CatalogReviewRequest
from .querysets import scoped, active_rows
from .resources import resource, RESOURCES
from .presentation import prepare_queryset, search_queryset, present
from .services import mutate, remove, StaleEdit
from .validation import safe_external_url
from .workspaces import selected_workspace,workspace_context


def throttle_key(request):
    value = f"{request.META.get('REMOTE_ADDR','')}:{request.POST.get('username','').casefold()[:254]}"
    return hashlib.sha256(value.encode()).hexdigest()


class ArtistAuthenticationForm(AuthenticationForm):
    def clean(self):
        row = LoginThrottle.objects.filter(key_hash=throttle_key(self.request)).first()
        if row and row.window_started > timezone.now()-timedelta(minutes=15) and row.failures >= 8:
            raise forms.ValidationError('Too many sign-in attempts. Please try again in 15 minutes.')
        return super().clean()


class ArtistLoginView(LoginView):
    template_name = 'portal/login.html'
    authentication_form = ArtistAuthenticationForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        now = timezone.now()
        with transaction.atomic():
            row,_ = LoginThrottle.objects.select_for_update().get_or_create(key_hash=throttle_key(self.request),defaults={'window_started':now})
            if row.window_started < now-timedelta(minutes=15):
                row.failures = 0
                row.window_started = now
            row.failures = min(row.failures+1,32767)
            row.save()
            LoginThrottle.objects.filter(window_started__lt=now-timedelta(days=1)).delete()
        return super().form_invalid(form)

    def form_valid(self, form):
        LoginThrottle.objects.filter(key_hash=throttle_key(self.request)).delete()
        return super().form_valid(form)


class ArtistLogoutView(LogoutView):
    pass


def context(request, artist=None, **values):
    return {**workspace_context(request,artist),'artist':artist,'can_edit':bool(artist and can_edit(request.user,artist)),**values}


def cover_url(request, artist, obj):
    assets = scoped(request.user,artist,'assets')
    if isinstance(obj, Asset):
        asset = obj if assets.filter(pk=obj.pk).exists() else None
    else:
        links = RecordingAsset if obj._meta.model_name == 'recording' else ReleaseAsset
        key = 'recording' if obj._meta.model_name == 'recording' else 'release'
        relation = active_rows(links.objects.filter(**{key:obj},purpose='cover',asset__in=assets)).order_by('-is_primary','id').first()
        asset = relation.asset if relation else None
        if asset is None and key == 'recording':
            release_ids = scoped(request.user,artist,'tracks').filter(recording=obj).values('release_id')
            relation = active_rows(ReleaseAsset.objects.filter(release_id__in=release_ids,purpose='cover',asset__in=assets)).order_by('-is_primary','id').first()
            asset = relation.asset if relation else None
    if not asset:
        return ''
    if asset.current_version_id and asset.current_version.preview_file:
        return reverse('portal:asset-media',args=[artist.pk,asset.pk,'preview'])
    if asset.mime_type not in ('image/jpeg','image/png','image/webp','image/gif','image/avif'):
        return ''
    try:
        return safe_external_url(asset.source_url) or ''
    except ValidationError:
        return ''


def decorate(request, artist, obj, kind):
    if kind in ('recordings','releases'):
        obj.detail_url = reverse('portal:recording' if kind == 'recordings' else 'portal:release',args=[artist.pk,obj.pk])
        obj.cover_url = cover_url(request,artist,obj)
        set_credit_display(obj)
        from .catalog_display import duration
        obj.duration_display=duration(obj.duration_ms) if kind=='recordings' else ''
    else:
        obj.detail_url = reverse('portal:detail',args=[artist.pk,kind,obj.pk])
    return obj


def set_credit_display(obj):
    credits=active_rows(obj.credits.filter(artist__organization_id=obj.organization_id,artist__archived_at__isnull=True)).select_related('artist').order_by('billing_order','pk')
    obj.artist_display=''.join((credit.credited_as or credit.artist.name)+(credit.join_phrase or ' · ') for credit in credits).removesuffix(' · ') or 'Credits pending'


@login_required
@require_GET
def overview(request):
    from .dashboard import dashboard_for
    from .audio_presentation import artist_audio
    artist = selected_workspace(request)
    if not artist:
        return render(request,'portal/no_access.html',context(request))
    counts = {kind:scoped(request.user,artist,kind).count() for kind in ('recordings','releases','assets','fanlinks')}
    recent = [decorate(request,artist,row,'recordings') for row in scoped(request.user,artist,'recordings').order_by(F('original_release_date').desc(nulls_last=True), 'title', 'pk')[:8]]
    return render(request,'portal/overview.html',context(request,artist,audio_profile=artist_audio(request.user,artist),dashboard=dashboard_for(request.user,artist,portfolio=request.GET.get('metrics_scope')=='all'),counts=counts,recordings=recent,recent_recordings=recent,review_pending=CatalogReviewRequest.objects.filter(artist=artist,status='pending').exists(),request_review_url=reverse('portal:request-review',args=[artist.pk]),recent_changes=EditorialChange.objects.filter(artist=artist).order_by('-created_at')[:6]))


@login_required
@require_GET
def catalog(request, artist_id):
    from .catalog_filters import catalog_selection
    artist = artist_for(request.user,artist_id)
    rows,filters = catalog_selection(request.user,artist,request.GET)
    kind = filters['kind']
    page = Paginator(rows,24).get_page(request.GET.get('page'))
    decorated = [decorate(request,artist,row,kind) for row in page]
    return render(request,'portal/catalog.html',context(request,artist,recordings=decorated,releases=decorated if kind=='releases' else [],page_obj=page,**filters))


def platform_links(request,artist,kind,obj):
    parent = 'recording' if kind == 'recording-links' else 'release'
    result = []
    for link in scoped(request.user,artist,kind).filter(**{parent:obj}).select_related('platform_object__service'):
        platform = link.platform_object
        try:
            url = safe_external_url(platform.url)
        except ValidationError:
            url = ''
        result.append({'service':platform.service.name,'logo_url':brand_logo(platform.service.slug,'social') or brand_logo(platform.service.slug),'label':platform.title or platform.service.name,'url':url,'review_status':link.review_status,'relationship_type':getattr(link,'relationship_type','full_release').replace('_',' ').capitalize()})
    return result


def asset_context(request,artist,links):
    result=[]
    accessible=scoped(request.user,artist,'assets')
    for asset in accessible.filter(pk__in=active_rows(links).values('asset_id')).select_related('current_version'):
        decorate(request,artist,asset,'assets')
        asset.label=asset.display_name or 'Untitled asset'
        asset.cover_url=cover_url(request,artist,asset)
        asset.available=bool(asset.current_version_id)
        asset.download_url=reverse('portal:asset-media',args=[artist.pk,asset.pk,'download']) if asset.available else ''
        result.append(asset)
    return result


@login_required
@require_GET
def recording_detail(request,artist_id,object_id):
    from .catalog_display import details
    artist=artist_for(request.user,artist_id)
    recording=get_object_or_404(scoped(request.user,artist,'recordings'),pk=object_id)
    return render(request,'portal/recording_detail.html',context(request,artist,**details(request,artist,'recordings',recording)))

@login_required
@require_GET
def release_detail(request,artist_id,object_id):
    from .catalog_display import details
    artist=artist_for(request.user,artist_id)
    release=get_object_or_404(scoped(request.user,artist,'releases'),pk=object_id)
    return render(request,'portal/release_detail.html',context(request,artist,**details(request,artist,'releases',release)))

def label(obj):
    return present(obj)['label']


@login_required
@require_GET
def resource_list(request,artist_id,kind):
    artist=artist_for(request.user,artist_id)
    spec=resource(kind)
    if kind=='assets':
        return asset_list(request,artist)
    objects=prepare_queryset(scoped(request.user,artist,kind),kind).order_by('-updated_at','pk')
    query=request.GET.get('q','')[:200]
    objects=search_queryset(objects,kind,query)
    page=Paginator(objects,30).get_page(request.GET.get('page'))
    rows=[]
    for obj in page:
        detail_url=reverse('portal:recording' if kind=='recordings' else 'portal:release',args=[artist.pk,obj.pk]) if kind in {'recordings','releases'} else reverse('portal:detail',args=[artist.pk,kind,obj.pk])
        rows.append({**present(obj,kind),'detail_url':detail_url,'cover_url':''})
    return render(request,'portal/resource_list.html',context(request,artist,title=spec.title,resource_name=kind,rows=rows,page_obj=page,query=query,create_url=reverse('portal:create',args=[artist.pk,kind]) if kind != 'artists' else ''))


def asset_list(request,artist):
    shared=request.GET.get('library')=='shared'
    query=request.GET.get('q','')[:200]
    category=request.GET.get('category','')
    categories=LIBRARY_CATEGORIES if shared else list(Asset.Category.choices)
    if category not in dict(categories):
        category=''
    visibilities=list(Asset._meta.get_field('visibility').choices)
    visibility=request.GET.get('visibility','')
    if visibility not in dict(visibilities):
        visibility=''
    sort=request.GET.get('sort','newest')
    if sort not in ('newest','name','type','-type','visibility','-visibility'):
        sort='newest'
    if shared:
        objects=[row for row in library_rows() if (not category or row['category']==category) and query.casefold() in row['label'].casefold()]
        if visibility and visibility!='public':
            objects=[]
        for row in objects:
            row.update(visibility='public',visibility_label='Public')
        objects.sort(key=lambda row:row['label'])
        if sort in ('type','-type'):
            objects.sort(key=lambda row:row['category_label'],reverse=sort=='-type')
    else:
        objects=scoped(request.user,artist,'assets').select_related('current_version')
        if category:
            objects=objects.filter(category=category)
        if visibility:
            objects=objects.filter(visibility=visibility)
        if query:
            objects=objects.filter(Q(display_name__icontains=query)|Q(description__icontains=query))
        # Sort by the displayed category label rather than the stored enum key.
        from django.db.models import Case,When,Value,CharField
        if sort in ('type','-type'):
            objects=objects.annotate(category_sort=Case(*[When(category=value,then=Value(title)) for value,title in Asset.Category.choices],default=Value('Uncategorized'),output_field=CharField()))
        ordering={'newest':('-updated_at','pk'),'name':('display_name','pk'),'type':('category_sort','display_name','pk'),'-type':('-category_sort','display_name','pk'),'visibility':('visibility','display_name','pk'),'-visibility':('-visibility','display_name','pk')}
        objects=objects.order_by(*ordering[sort])
    page=Paginator(objects,30).get_page(request.GET.get('page'))
    rows=list(page) if shared else [dict(label=label(obj),detail_url=reverse('portal:detail',args=[artist.pk,'assets',obj.pk]),subtitle=obj.mime_type or 'File reference',cover_url=cover_url(request,artist,obj),icon=asset_icon(obj),category_label=obj.get_category_display() or 'Uncategorized',visibility=obj.visibility,visibility_label=obj.get_visibility_display(),status='',shared=False) for obj in page]
    from urllib.parse import urlencode
    params={'q':query,'category':category,'sort':sort,'visibility':visibility}
    if shared:
        params['library']='shared'
    type_sort_url='?'+urlencode({**params,'sort':'-type' if sort=='type' else 'type'})
    return render(request,'portal/asset_list.html',context(request,artist,title='Assets',resource_name='assets',rows=rows,page_obj=page,query=query,category=category,categories=categories,visibility=visibility,visibilities=visibilities,sort=sort,shared=shared,pagination_query=urlencode(params),type_sort_url=type_sort_url,create_url='' if shared else reverse('portal:create',args=[artist.pk,'assets'])))


@login_required
@require_GET
def artist_detail(request,artist_id):
    artist=artist_for(request.user,artist_id)
    sections=[{'label':RESOURCES[kind].title,'title':RESOURCES[kind].title,'url':reverse('portal:resources',args=[artist.pk,kind]),'count':scoped(request.user,artist,kind).count()} for kind in RESOURCES if kind not in ('artists','recordings','releases','assets','fanlinks')]
    from publishing.models import Website
    websites=[]
    if request.user.is_staff:
        for website in Website.objects.filter(artist=artist,archived_at__isnull=True):
            website.review_url=reverse('portal:review',args=[artist.pk,website.pk])
            websites.append(website)
    return render(request,'portal/artist_detail.html',context(request,artist,profile_sections=sections,review_websites=websites))


@login_required
@require_http_methods(['GET','POST'])
def edit_resource(request,artist_id,kind,object_id=None):
    if kind in ('recordings','releases'):
        from .catalog_views import edit_catalog
        return edit_catalog(request,artist_id,kind,object_id)
    artist=artist_for(request.user,artist_id)
    require_editor(request.user,artist)
    spec=resource(kind)
    instance=get_object_or_404(scoped(request.user,artist,kind,write=True),pk=object_id) if object_id else None
    if kind=='artists' and not instance:
        raise Http404
    back_url=reverse('portal:resources',args=[artist.pk,kind])
    form=make_form(kind,request.user,artist,instance=instance)
    status=200
    if request.method=='POST':
        try:
            obj,form=mutate(request.user,artist,kind,request.POST,request.FILES,object_id)
            if obj:
                messages.success(request,'Changes saved. Use Publish updates when ready.')
                return redirect('portal:detail',artist_id=artist.pk,kind=kind,object_id=obj.pk)
        except (ValidationError,OSError) as error:
            form=make_form(kind,request.user,artist,data=request.POST,files=request.FILES,instance=instance)
            form.is_valid()
            form.add_error(None,error.messages if isinstance(error,ValidationError) else 'File storage is unavailable. Please try again later.')
            status=409 if isinstance(error,StaleEdit) else 400
    return render(request,'portal/form.html',context(request,artist,form=form,title=f'{"Edit" if instance else "Add"} {spec.title.lower()}',back_url=back_url,delete_url=reverse('portal:delete',args=[artist.pk,kind,instance.pk]) if instance else ''),status=status)


@login_required
@require_GET
def resource_detail(request,artist_id,kind,object_id):
    artist=artist_for(request.user,artist_id)
    obj=get_object_or_404(scoped(request.user,artist,kind),pk=object_id)
    if kind in ('recordings','releases'):
        return redirect('portal:recording' if kind=='recordings' else 'portal:release',artist_id=artist.pk,object_id=obj.pk)
    if kind=='fanlinks':
        destinations=[]
        for row in scoped(request.user,artist,'destinations').filter(fanlink_page=obj).select_related('platform_object__service').order_by('sort_order','pk'):
            try:
                url=safe_external_url(row.platform_object.url)
            except ValidationError:
                url=''
            destinations.append({'label':row.label or row.platform_object.service.name,'service':row.platform_object.service.name,'logo_url':brand_logo(row.platform_object.service.slug),'url':url,'enabled':row.enabled})
        return render(request,'portal/fanlink_detail.html',context(request,artist,fanlink=obj,destinations=destinations,can_edit=can_edit(request.user,artist)))
    form=make_form(kind,request.user,artist,instance=obj,read_only=True)
    for field in form.fields.values():
        field.disabled=True
    downloadable=kind=='assets' and bool(obj.current_version_id)
    return render(request,'portal/form.html',context(request,artist,form=form,title=label(obj),back_url=reverse('portal:resources',args=[artist.pk,kind]),read_only=True,edit_url=reverse('portal:edit',args=[artist.pk,kind,obj.pk]) if can_edit(request.user,artist) and scoped(request.user,artist,kind,write=True).filter(pk=obj.pk).exists() else '',download_url=reverse('portal:asset-media',args=[artist.pk,obj.pk,'download']) if downloadable else '',preview_url=cover_url(request,artist,obj) if kind=='assets' else '',preview_alt=getattr(obj,'alt_text','') if kind=='assets' else '',text_preview=text_preview(obj) if kind=='assets' else None))


@login_required
@require_http_methods(['GET','POST'])
def delete_resource(request,artist_id,kind,object_id):
    artist=artist_for(request.user,artist_id)
    require_editor(request.user,artist)
    obj=get_object_or_404(scoped(request.user,artist,kind,write=True),pk=object_id)
    back_url=reverse('portal:detail',args=[artist.pk,kind,obj.pk])
    if request.method=='POST':
        try:
            remove(request.user,artist,kind,object_id,request.POST.get('version'))
        except StaleEdit as error:
            return render(request,'portal/confirm_delete.html',context(request,artist,object=obj,title=label(obj),back_url=back_url,version=stamp(obj),error=error.messages[0]),status=409)
        messages.success(request,'Removed from your workspace. Original evidence has been preserved.')
        if kind in ('recordings','releases'):
            url=reverse('portal:catalog',args=[artist.pk])
            return redirect(url+('?kind=releases' if kind=='releases' else ''))
        return redirect('portal:overview') if kind=='artists' else redirect('portal:resources',artist_id=artist.pk,kind=kind)
    impact=[]
    if kind in ('recordings','releases'):
        stem='recording' if kind=='recordings' else 'release'
        impact=[f'{scoped(request.user,artist,"tracks").filter(**{stem:obj}).count()} track appearances',f'{scoped(request.user,artist,"fanlinks").filter(**{stem:obj}).count()} fanlinks']
    return render(request,'portal/confirm_delete.html',context(request,artist,object=obj,title=label(obj),back_url=back_url,version=stamp(obj),impact=impact))


@login_required
@require_GET
def asset_media(request,artist_id,object_id,action):
    artist=artist_for(request.user,artist_id)
    asset=get_object_or_404(scoped(request.user,artist,'assets').select_related('current_version'),pk=object_id)
    if not asset.current_version_id or action not in ('preview','download'):
        raise Http404
    from .media import file_response
    return file_response(request,asset.current_version,preview=action=='preview')


@login_required
@require_http_methods(['POST'])
def request_review(request,artist_id):
    artist=artist_for(request.user,artist_id)
    require_editor(request.user,artist)
    CatalogReviewRequest.objects.get_or_create(artist=artist,status='pending',defaults={'requested_by':request.user})
    messages.success(request,'Your catalog is queued for staff review. Your current approved content stays unchanged.')
    return redirect(reverse('portal:overview')+f'?artist={artist.pk}')
