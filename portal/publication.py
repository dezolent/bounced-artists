"""One portal workflow for publishing drafts or requesting optional staff review."""
import hashlib
import json
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from artists.models import Artist, ArtistMembership, ArtistResourceGrant
from catalog.models import Recording, Release, Asset, ReleaseGroup, RecordingPlatformMatch, ReleasePlatformMatch
from publishing.models import Website, WebsiteApprovedState, FanlinkPage
from publishing.projection import Builder, normalize_selection, require_publisher, store_revision, public_id, validate_public_url
from provenance.models import AuditEvent
from .access import artist_for, require_editor
from .models import CatalogReviewRequest
from .querysets import scoped, active_rows
from .services import StaleEdit


SELECTION_MODELS={'recordings':Recording,'releases':Release,'artwork':Asset,'lyrics':Asset,'fanlinks':FanlinkPage}
SALT='portal-publication-v1'


def publication_form(user,artist,data=None,initial=None):
    from .presentation import entity_label, prepare_queryset
    fields={'website':forms.ModelChoiceField(queryset=Website.objects.filter(artist=artist,organization=artist.organization,archived_at__isnull=True))}
    for key,kind in [('recordings','recordings'),('releases','releases'),('fanlinks','fanlinks'),('artwork','assets'),('lyrics','assets'),('works','works')]:
        rows=scoped(user,artist,kind,write=not user.is_staff)
        if key in ('artwork','lyrics'):
            rows=rows.filter(current_version__isnull=False).exclude(rights_basis='')
            if key=='lyrics':
                rows=rows.filter(category='lyrics_text',current_version__mime_type='text/plain')
            else:
                rows=rows.filter(current_version__mime_type__in=['image/jpeg','image/png','image/webp'])
        fields[key]=forms.ModelMultipleChoiceField(queryset=prepare_queryset(rows),required=False,widget=forms.CheckboxSelectMultiple)
        fields[key].label_from_instance=entity_label
    fields['confirm_rights']=forms.BooleanField(required=False,label='I have permission to publish the selected artwork and the full text of the selected lyrics.')
    cls=type('PublicationForm',(forms.Form,),fields)
    form=cls(data=data,initial=initial)
    return form


def checked_selection(user,artist,website,selection):
    """Recheck every selected grant, even for assets unused in the final output."""
    selection=normalize_selection(selection)
    for key,kind in [('recordings','recordings'),('releases','releases'),('fanlinks','fanlinks'),('artwork','assets'),('lyrics','assets'),('works','works')]:
        ids=selection[key]
        rows=scoped(user,artist,kind,write=not user.is_staff).filter(pk__in=ids)
        if key=='fanlinks': rows=rows.filter(website=website)
        if rows.count()!=len(ids):
            raise ValidationError('A selected item is no longer available to publish in this workspace.')
    if not user.is_staff:
        dependent_groups=ReleaseGroup.objects.filter(pk__in=Release.objects.filter(pk__in=selection['releases']).values('release_group_id')).exclude(status='approved')
        for group in dependent_groups:
            if not ArtistResourceGrant.objects.filter(artist=artist,release_group=group,capability='manage').exists():
                raise ValidationError('An editor needs edit access to a draft release group before publishing its release. Ask staff to assign access or review the update.')
    return selection


def make_preview(user,artist,website,selection,rights_confirmed):
    require_publisher(user,website,request_only=True)
    selection=checked_selection(user,artist,website,selection)
    if (selection['artwork'] or selection['lyrics']) and not rights_confirmed:
        raise ValidationError('Confirm your permission to publish the selected artwork and lyrics.')
    return Builder(website,selection,publication=True,rights_confirmed=rights_confirmed).build()


def review_snapshot(artist):
    values=[]
    for item in CatalogReviewRequest.objects.filter(artist=artist,status__in=['pending','returned']).order_by('pk'):
        event=AuditEvent.objects.filter(organization=artist.organization).filter(Q(change_json__review_request_id=str(item.pk))|Q(change_json__request_id=item.pk)).order_by('-created_at','-pk').values_list('pk',flat=True).first()
        values.append({'id':item.pk,'website':str(item.website_id),'requested_by':item.requested_by_id,'status':item.status,'selection':item.publication_selection,'event':str(event)})
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def token_for(user,artist,website,preview,rights_confirmed):
    return signing.dumps({'actor':user.pk,'artist':str(artist.pk),'website':str(website.pk),
                         'selection':preview.selection,'checksum':preview.checksum,'source_checksum':preview.source_checksum,
                         'rights_confirmed':bool(rights_confirmed),'requires_staff_review':artist.requires_staff_review,
                         'review_snapshot':review_snapshot(artist),
                         'active_revision':str(WebsiteApprovedState.objects.filter(website=website).values_list('active_revision_id',flat=True).first())},salt=SALT,compress=True)


def load_token(value,user,artist):
    try:
        payload=signing.loads(value,salt=SALT,max_age=3600)
        if payload['actor']!=user.pk or payload['artist']!=str(artist.pk): raise ValueError
        return payload
    except (signing.BadSignature,ValueError,TypeError,KeyError):
        raise StaleEdit('This preview expired or belongs to another account. Preview your updates again.') from None


@transaction.atomic
def complete_publication(user,artist_id,value,*,request_review=False):
    artist=Artist.objects.select_for_update().get(pk=artist_id,archived_at__isnull=True)
    user=get_user_model().objects.select_for_update().get(pk=user.pk)
    list(ArtistMembership.objects.select_for_update().filter(artist=artist,user=user))
    list(ArtistResourceGrant.objects.select_for_update().filter(artist=artist).order_by('pk'))
    payload=load_token(value,user,artist)
    website=get_object_or_404(Website.objects.select_for_update(),pk=payload['website'],artist=artist,organization=artist.organization,archived_at__isnull=True)
    require_publisher(user,website,request_only=request_review)
    if payload['review_snapshot']!=review_snapshot(artist):
        raise StaleEdit('The review request changed after this preview. Review the latest request before continuing.')
    active_revision=str(WebsiteApprovedState.objects.filter(website=website).values_list('active_revision_id',flat=True).first())
    if payload['active_revision']!=active_revision:
        raise StaleEdit('Another version was published or withdrawn after this preview. Preview the current selection again.')
    if payload['requires_staff_review']!=artist.requires_staff_review:
        raise StaleEdit('The workspace publication policy changed. Preview your updates again.')
    selection=checked_selection(user,artist,website,payload['selection'])
    current=Builder(website,selection,lock=True,publication=True,rights_confirmed=payload['rights_confirmed']).build()
    if current.checksum!=payload['checksum'] or current.source_checksum!=payload['source_checksum']:
        raise StaleEdit('Content changed after this preview. Preview the latest updates before continuing.')
    if request_review:
        request,_=CatalogReviewRequest.objects.get_or_create(artist=artist,status='pending',defaults={'requested_by':user})
        request.website=website
        request.requested_by=user
        request.publication_selection={**selection,'rights_confirmed':payload['rights_confirmed']}
        request.full_clean();request.save()
        AuditEvent.objects.create(organization=artist.organization,user=user,action='portal.publication.request',target_label=artist.name,change_json={'request_id':request.pk,'website_id':str(website.pk)})
        return None
    # Readiness flags are part of this explicit publication, not separate admin steps.
    for key,model in [('recordings',Recording),('releases',Release),('fanlinks',FanlinkPage)]:
        for obj in model.objects.select_for_update().filter(pk__in=selection[key]).order_by('pk'):
            obj.status='approved';obj.save(update_fields=['status','updated_at'])
    groups=ReleaseGroup.objects.filter(pk__in=Release.objects.filter(pk__in=selection['releases']).values('release_group_id'))
    for group in groups:
        group.status='approved';group.save(update_fields=['status','updated_at'])
    for model,parent in [(RecordingPlatformMatch,'recording'),(ReleasePlatformMatch,'release')]:
        included={(item['id'],link['service'],link['url']) for item in current.payload[parent+'s'] for link in item['platform_links']}
        matches=active_rows(model.objects.select_for_update().filter(**{f'{parent}_id__in':selection[parent+'s']},review_status='pending')).order_by('pk')
        for match in matches:
            platform=match.platform_object
            try: url=validate_public_url(platform.service.slug,platform.url)
            except ValidationError: continue
            if (public_id(parent,getattr(match,f'{parent}_id')),platform.service.slug,url) not in included:
                continue
            match.review_status='accepted';match.save(update_fields=['review_status','updated_at'])
    if payload['rights_confirmed']:
        for asset in Asset.objects.select_for_update().filter(pk__in=set(selection['artwork']+selection['lyrics'])).order_by('pk'):
            asset.visibility='public';asset.rights_approved=True;asset.save(update_fields=['visibility','rights_approved','updated_at'])
    final=Builder(website,selection,lock=True).build()
    if final.payload!=current.payload:
        raise ValidationError('The prepared public content differs from the preview. Nothing was published.')
    revision=store_revision(user,website,final)
    for review in CatalogReviewRequest.objects.select_for_update().filter(Q(website=website)|Q(website__isnull=True),artist=artist,status__in=['pending','returned']):
        previous=review.status
        review.status='resolved';review.save(update_fields=['status'])
        AuditEvent.objects.create(organization=artist.organization,user=user,action='portal.review_request',target_label=artist.name,
                                  change_json={'review_request_id':str(review.pk),'previous_status':previous,'status':'resolved','decision':'published','notes':f'Published reviewed catalog for {website.domain}.'})
    return revision


def withdraw_snapshot(artist):
    return list(WebsiteApprovedState.objects.filter(website__artist=artist).order_by('website_id').values_list('website_id','active_revision_id'))


def readable_preview(preview):
    """Render the public allowlist in human terms without exposing private records."""
    if preview is None: return []
    groups=[]
    for kind in ('recordings','releases','fanlinks'):
        for item in preview.payload[kind]:
            details=[]
            for title,key in [('Version','version_title'),('Type','recording_type' if kind=='recordings' else 'release_type'),('Release date','original_release_date' if kind=='recordings' else 'release_date'),('Language','language'),('Edition','edition'),('Country','country'),('Barcode','barcode'),('Catalog number','catalog_number'),('Packaging','packaging'),('Disambiguation','disambiguation')]:
                if item.get(key): details.append((title,str(item[key])))
            if item.get('duration_ms') is not None:
                seconds=item['duration_ms']//1000;details.append(('Duration',f'{seconds//60}:{seconds%60:02}'))
            for title,key,formatter in [('Artists','artists',lambda row:f"{row['name']} ({row['role']})"),('Genres','genres',lambda row:row['name']),('Identifiers','identifiers',lambda row:f"{row['scheme']}: {row['value']}"),('Labels','labels',lambda row:row['name']),('Related works','works',lambda row:row['title'])]:
                if item.get(key): details.append((title,', '.join(formatter(row) for row in item[key])))
            if item.get('tracks'): details.append(('Tracks',str(len(item['tracks']))))
            group=item.get('release_group')
            if group: details.append(('Release group',group['title']))
            groups.append({'title':item['title'],'kind':{'recordings':'Song','releases':'Release','fanlinks':'Fanlink'}[kind],
                           'details':details,'destinations':item.get('platform_links',item.get('destinations',[])),
                           'lyrics':item.get('lyrics',[])})
    return groups


@transaction.atomic
def withdraw_publication(user,artist,value):
    artist=Artist.objects.select_for_update().get(pk=artist.pk,archived_at__isnull=True)
    user=get_user_model().objects.select_for_update().get(pk=user.pk)
    require_editor(user,artist)
    if not user.is_active: raise PermissionDenied
    try:
        snapshot=signing.loads(value,salt='portal-withdraw',max_age=3600)
    except signing.BadSignature:
        raise StaleEdit('Reload before withdrawing public access.') from None
    websites=list(Website.objects.select_for_update().filter(artist=artist).order_by('pk'))
    expected={'actor':user.pk,'artist':str(artist.pk),'states':[[str(site),str(revision)] for site,revision in withdraw_snapshot(artist)]}
    if snapshot!=expected: raise StaleEdit('Publication changed. Reload before withdrawing public access.')
    WebsiteApprovedState.objects.filter(website__in=websites).update(active_revision=None)
    AuditEvent.objects.create(organization=artist.organization,user=user,action='portal.publication.withdraw',target_label=artist.name,change_json={'artist_id':str(artist.pk)})


@login_required
@require_http_methods(['GET','POST'])
def publish(request,artist_id):
    from .views import context
    artist=artist_for(request.user,artist_id)
    require_editor(request.user,artist)
    initial={}
    if request.method=='GET':
        requests=CatalogReviewRequest.objects.filter(artist=artist)
        requested_id=request.GET.get('request','')
        pending=requests.filter(pk=requested_id).first() if requested_id.isdecimal() else requests.filter(status__in=['pending','returned']).order_by('-created_at','-pk').first()
        if pending and pending.website_id:
            initial={key:value for key,value in pending.publication_selection.items() if key!='rights_confirmed'}
            initial['website']=pending.website_id
        else:
            for key,kind in [('recordings','recordings'),('releases','releases'),('fanlinks','fanlinks'),('works','works')]:
                initial[key]=list(scoped(request.user,artist,kind,write=not request.user.is_staff).values_list('pk',flat=True))
            initial['website']=Website.objects.filter(artist=artist,archived_at__isnull=True).values_list('pk',flat=True).first()
    form=publication_form(request.user,artist,request.POST if request.method=='POST' and request.POST.get('action')=='preview' else None,initial)
    if request.method=='GET' and not (pending and pending.website_id):
        for key in ('artwork','lyrics'):
            form.initial[key]=list(form.fields[key].queryset.values_list('pk',flat=True))
    preview=None;token=None;status=200
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action in ('publish','request-review'):
                complete_publication(request.user,artist.pk,request.POST.get('publication_token',''),request_review=action=='request-review')
                messages.success(request,'Updates sent for staff review. Your published content stays unchanged.' if action=='request-review' else 'Updates published. Your website can now read this version.')
                return redirect('portal:publish',artist_id=artist.pk)
            if action=='withdraw':
                withdraw_publication(request.user,artist,request.POST.get('withdraw_token',''))
                messages.success(request,'Public API access withdrawn. Saved catalog data is preserved.')
                return redirect('portal:publish',artist_id=artist.pk)
            if action!='preview': raise ValidationError('Choose Preview updates before publishing.')
            if form.is_valid():
                allowed=set(form.fields)|{'action','csrfmiddlewaretoken'}
                if set(request.POST)-allowed: raise ValidationError('Unexpected publication fields.')
                website=form.cleaned_data['website']
                selection={key:[str(obj.pk) for obj in form.cleaned_data[key]] for key in ('recordings','releases','fanlinks','artwork','lyrics','works')}
                preview=make_preview(request.user,artist,website,selection,form.cleaned_data['confirm_rights'])
                token=token_for(request.user,artist,website,preview,form.cleaned_data['confirm_rights'])
            else: status=400
        except ValidationError as error:
            if not form.is_bound: form=publication_form(request.user,artist,{},initial)
            form.is_valid();form.add_error(None,error.messages)
            status=409 if isinstance(error,StaleEdit) else 400
    states=list(WebsiteApprovedState.objects.filter(website__artist=artist).select_related('website','active_revision'))
    latest_request=CatalogReviewRequest.objects.filter(artist=artist).order_by('-created_at','-pk').first()
    returned=latest_request if latest_request and latest_request.status=='returned' else None
    feedback=None
    if returned:
        decision=AuditEvent.objects.filter(organization=artist.organization,action='portal.review_request',change_json__review_request_id=str(returned.pk),change_json__status='returned').order_by('-created_at','-pk').first()
        feedback=decision.change_json.get('notes','') if decision else 'Staff requested changes. Update your catalog and send a fresh preview.'
    withdraw_token=signing.dumps({'actor':request.user.pk,'artist':str(artist.pk),'states':[(str(site),str(revision)) for site,revision in withdraw_snapshot(artist)]},salt='portal-withdraw')
    return render(request,'portal/publication.html',context(request,artist,form=form,publication_preview=preview,publication_token=token,
                  preview_items=readable_preview(preview),
                  publication_states=states,has_publication=any(row.active_revision_id for row in states),withdraw_token=withdraw_token,
                  review_feedback=feedback,
                  pending_review=CatalogReviewRequest.objects.filter(artist=artist,status='pending').exists()),status=status)


@login_required
@require_http_methods(['GET','POST'])
def publication_settings(request,artist_id):
    from .views import context
    artist=artist_for(request.user,artist_id)
    if not request.user.is_staff: raise PermissionDenied
    class PolicyForm(forms.ModelForm):
        version=forms.CharField(widget=forms.HiddenInput)
        class Meta:
            model=Artist
            fields=['requires_staff_review']
    from .forms import stamp
    from .services import _check_version
    form=PolicyForm(request.POST if request.method=='POST' else None,instance=artist,initial={'version':stamp(artist)})
    status=200
    if request.method=='POST' and form.is_valid():
        try:
            if set(request.POST)-set(form.fields)-{'csrfmiddlewaretoken'}:
                raise ValidationError('Unexpected workflow settings.')
            with transaction.atomic():
                artist=Artist.objects.select_for_update().get(pk=artist.pk)
                actor=get_user_model().objects.select_for_update().get(pk=request.user.pk)
                if not actor.is_active or not actor.is_staff: raise PermissionDenied
                _check_version(form.cleaned_data['version'],artist)
                artist.requires_staff_review=form.cleaned_data['requires_staff_review']
                artist.save(update_fields=['requires_staff_review','updated_at'])
                AuditEvent.objects.create(organization=artist.organization,user=actor,action='portal.publication.policy',target_label=artist.name,change_json={'requires_staff_review':artist.requires_staff_review})
            messages.success(request,'Publication workflow updated.')
            return redirect('portal:publish',artist_id=artist.pk)
        except ValidationError as error:
            form.add_error(None,error.messages);status=409 if isinstance(error,StaleEdit) else 400
    elif request.method=='POST': status=400
    return render(request,'portal/publication_settings.html',context(request,artist,form=form),status=status)
