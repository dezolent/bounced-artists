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
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from rest_framework.generics import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from artists.models import Artist
from catalog.models import Asset, AssetVersion, RecordingAsset, ReleaseAsset
from .access import artists_for, artist_for, can_edit, require_editor
from .forms import make_form, stamp
from .models import LoginThrottle, EditorialChange, CatalogReviewRequest
from .querysets import scoped, active_rows
from .resources import resource, RESOURCES
from .services import mutate, remove, StaleEdit
from .validation import safe_external_url


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
    return {'artists':artists_for(request.user),'artist':artist,'can_edit':bool(artist and can_edit(request.user,artist)),**values}


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
    try:
        return safe_external_url(asset.source_url) or ''
    except ValidationError:
        return ''


def decorate(request, artist, obj, kind):
    if kind in ('recordings','releases'):
        obj.detail_url = reverse('portal:recording' if kind == 'recordings' else 'portal:release',args=[artist.pk,obj.pk])
        obj.cover_url = cover_url(request,artist,obj)
    else:
        obj.detail_url = reverse('portal:detail',args=[artist.pk,kind,obj.pk])
    return obj


@login_required
@require_GET
def overview(request):
    available = artists_for(request.user)
    artist = artist_for(request.user,request.GET['artist']) if request.GET.get('artist') else available.first()
    if not artist:
        return render(request,'portal/no_access.html',context(request))
    counts = {kind:scoped(request.user,artist,kind).count() for kind in ('recordings','releases','assets','fanlinks')}
    recent = [decorate(request,artist,row,'recordings') for row in scoped(request.user,artist,'recordings').order_by('-updated_at')[:8]]
    return render(request,'portal/overview.html',context(request,artist,counts=counts,recordings=recent,recent_recordings=recent,review_pending=CatalogReviewRequest.objects.filter(artist=artist,status='pending').exists(),request_review_url=reverse('portal:request-review',args=[artist.pk]),recent_changes=EditorialChange.objects.filter(artist=artist).order_by('-created_at')[:6]))


@login_required
@require_GET
def catalog(request, artist_id):
    artist = artist_for(request.user,artist_id)
    kind = 'releases' if request.GET.get('kind') == 'releases' else 'recordings'
    rows = scoped(request.user,artist,kind).order_by('title','pk')
    query = request.GET.get('q','')[:200]
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(identifiers__value__icontains=query)).distinct()
    status = request.GET.get('status','')
    if status in ('draft','review','approved'):
        rows = rows.filter(status=status)
    page = Paginator(rows,24).get_page(request.GET.get('page'))
    decorated = [decorate(request,artist,row,kind) for row in page]
    return render(request,'portal/catalog.html',context(request,artist,recordings=decorated,releases=decorated if kind=='releases' else [],kind=kind,query=query,page_obj=page))


def platform_links(request,artist,kind,obj):
    parent = 'recording' if kind == 'recording-links' else 'release'
    result = []
    for link in scoped(request.user,artist,kind).filter(**{parent:obj}).select_related('platform_object__service'):
        platform = link.platform_object
        try:
            url = safe_external_url(platform.url)
        except ValidationError:
            url = ''
        result.append({'service':platform.service.name,'label':platform.title or platform.service.name,'url':url,'review_status':link.review_status,'relationship_type':getattr(link,'relationship_type','full_release')})
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
    artist=artist_for(request.user,artist_id)
    recording=get_object_or_404(scoped(request.user,artist,'recordings'),pk=object_id)
    from catalog.models import ReleaseTrack
    releases=[decorate(request,artist,row,'releases') for row in scoped(request.user,artist,'releases').filter(pk__in=active_rows(ReleaseTrack.objects.filter(recording=recording)).values('release_id'))]
    fanlinks=[decorate(request,artist,row,'fanlinks') for row in scoped(request.user,artist,'fanlinks').filter(recording=recording)]
    return render(request,'portal/recording_detail.html',context(request,artist,recording=recording,cover_url=cover_url(request,artist,recording),credits=scoped(request.user,artist,'recording-credits').filter(recording=recording).select_related('artist').order_by('billing_order'),identifiers=scoped(request.user,artist,'recording-identifiers').filter(recording=recording),genres=scoped(request.user,artist,'recording-genres').filter(recording=recording).select_related('genre'),releases=releases,fanlinks=fanlinks,assets=asset_context(request,artist,RecordingAsset.objects.filter(recording=recording)),platform_links=platform_links(request,artist,'recording-links',recording),work_links=scoped(request.user,artist,'recording-works').filter(recording=recording).select_related('work'),can_edit=can_edit(request.user,artist) and scoped(request.user,artist,'recordings',write=True).filter(pk=recording.pk).exists()))


@login_required
@require_GET
def release_detail(request,artist_id,object_id):
    artist=artist_for(request.user,artist_id)
    release=get_object_or_404(scoped(request.user,artist,'releases'),pk=object_id)
    tracks=scoped(request.user,artist,'tracks').filter(release=release,recording__in=scoped(request.user,artist,'recordings')).select_related('recording').order_by('disc_number','track_number')
    for track in tracks:
        decorate(request,artist,track.recording,'recordings')
    return render(request,'portal/release_detail.html',context(request,artist,release=release,cover_url=cover_url(request,artist,release),tracks=tracks,credits=scoped(request.user,artist,'release-credits').filter(release=release).select_related('artist').order_by('billing_order'),identifiers=scoped(request.user,artist,'release-identifiers').filter(release=release),assets=asset_context(request,artist,ReleaseAsset.objects.filter(release=release)),platform_links=platform_links(request,artist,'release-links',release),fanlinks=[decorate(request,artist,row,'fanlinks') for row in scoped(request.user,artist,'fanlinks').filter(release=release)],can_edit=can_edit(request.user,artist) and scoped(request.user,artist,'releases',write=True).filter(pk=release.pk).exists()))


def label(obj):
    for attr in ('title','display_name','name','label','display_title','value','url','scheme','purpose'):
        value=getattr(obj,attr,None)
        if value:
            return str(value)
    return str(obj)


@login_required
@require_GET
def resource_list(request,artist_id,kind):
    artist=artist_for(request.user,artist_id)
    spec=resource(kind)
    objects=scoped(request.user,artist,kind).order_by('-updated_at','pk')
    query=request.GET.get('q','')[:200]
    if query:
        names={field.name for field in spec.model._meta.fields}
        lookup=Q()
        for name in ('title','display_name','name','label','display_title','value','url','scheme','purpose'):
            if name in names:
                lookup|=Q(**{f'{name}__icontains':query})
        objects=objects.filter(lookup) if lookup else objects.none()
    page=Paginator(objects,30).get_page(request.GET.get('page'))
    rows=[]
    for obj in page:
        decorate(request,artist,obj,kind)
        rows.append({'label':label(obj),'detail_url':obj.detail_url,'subtitle':getattr(obj,'description','') or getattr(obj,'mime_type',''),'cover_url':cover_url(request,artist,obj) if kind=='assets' else '', 'status':getattr(obj,'status','')})
    return render(request,'portal/resource_list.html',context(request,artist,title=spec.title,resource_name=kind,rows=rows,page_obj=page,query=query,create_url=reverse('portal:create',args=[artist.pk,kind]) if kind != 'artists' else ''))


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
                messages.success(request,'Changes saved. Public content changes only after review.')
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
            destinations.append({'label':row.label or row.platform_object.service.name,'service':row.platform_object.service.name,'url':url,'enabled':row.enabled})
        return render(request,'portal/fanlink_detail.html',context(request,artist,fanlink=obj,destinations=destinations,can_edit=can_edit(request.user,artist)))
    form=make_form(kind,request.user,artist,instance=obj,read_only=True)
    for field in form.fields.values():
        field.disabled=True
    downloadable=kind=='assets' and bool(obj.current_version_id)
    return render(request,'portal/form.html',context(request,artist,form=form,title=label(obj),back_url=reverse('portal:resources',args=[artist.pk,kind]),read_only=True,edit_url=reverse('portal:edit',args=[artist.pk,kind,obj.pk]) if can_edit(request.user,artist) and scoped(request.user,artist,kind,write=True).filter(pk=obj.pk).exists() else '',download_url=reverse('portal:asset-media',args=[artist.pk,obj.pk,'download']) if downloadable else '',preview_url=cover_url(request,artist,obj) if kind=='assets' else ''))


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
        return redirect('portal:overview') if kind=='artists' else redirect('portal:resources',artist_id=artist.pk,kind=kind)
    return render(request,'portal/confirm_delete.html',context(request,artist,object=obj,title=label(obj),back_url=back_url,version=stamp(obj)))


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
