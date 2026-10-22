import json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from catalog.models import Recording, Release, Asset
from publishing.models import Website, FanlinkPage
from rights.models import MusicalWork
from publishing.projection import preview_catalog, approve_catalog, revoke_catalog
from .access import artist_for
from .views import context
from .querysets import active_rows


@login_required
@require_http_methods(['GET','POST'])
def review(request,artist_id,website_id):
    if not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied
    artist=artist_for(request.user,artist_id)
    website=get_object_or_404(Website,pk=website_id,artist=artist,organization=artist.organization,archived_at__isnull=True)
    fields={}
    for key,model,grant in [('recordings',Recording,'recording'),('releases',Release,'release'),('artwork',Asset,'asset'),('lyrics',Asset,'asset'),('works',MusicalWork,'work')]:
        rows=active_rows(model.objects.filter(organization=artist.organization,artist_grants__artist=artist,archived_at__isnull=True))
        if key in ('artwork', 'lyrics'):
            rows=rows.filter(visibility='public',rights_approved=True,current_version__isnull=False).exclude(rights_basis='')
            if key == 'lyrics': rows=rows.filter(category='lyrics_text',current_version__mime_type='text/plain')
            else: rows=rows.filter(current_version__mime_type__in=['image/jpeg','image/png','image/webp'])
        elif key != 'works': rows=rows.filter(status='approved')
        help_text = {
            'lyrics': 'Select public, rights-approved text uploads attached to the selected songs or releases. The preview publishes the full approved text.',
            'works': 'Select assigned compositions to publish their title and public identifiers through verified song relationships.',
        }.get(key, 'Only assigned, staff-approved records are eligible.')
        fields[key]=forms.ModelMultipleChoiceField(queryset=rows,required=False,help_text=help_text)
    fields['fanlinks']=forms.ModelMultipleChoiceField(queryset=active_rows(FanlinkPage.objects.filter(website=website,status='approved',archived_at__isnull=True)),required=False)
    ReviewForm=type('ReviewForm',(forms.Form,),fields)
    form=ReviewForm(request.POST if request.method=='POST' else None)
    preview_json=None;token=None;status=200
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='revoke':
                revoke_catalog(request.user,website)
                messages.success(request,'Public API approval withdrawn.')
                return redirect('portal:review',artist_id=artist.pk,website_id=website.pk)
            if action=='approve':
                signed=signing.loads(request.POST.get('review_token',''),salt='catalog-review',max_age=3600)
                if signed['actor']!=request.user.pk or signed['website']!=str(website.pk):
                    raise ValidationError('This review belongs to another session.')
                preview=preview_catalog(request.user,website,signed['selection'])
                if preview.checksum!=signed['checksum'] or preview.source_checksum!=signed['source_checksum']:
                    raise ValidationError('Content changed after preview. Generate and review a fresh preview.')
                approve_catalog(request.user,website,preview)
                from .models import CatalogReviewRequest
                CatalogReviewRequest.objects.filter(artist=artist,status='pending').update(status='resolved')
                messages.success(request,'Reviewed content is now available through the public API.')
                return redirect('portal:review',artist_id=artist.pk,website_id=website.pk)
            if action=='preview' and form.is_valid():
                selection={key:[str(obj.pk) for obj in value] for key,value in form.cleaned_data.items()}
                preview=preview_catalog(request.user,website,selection)
                preview_json=json.dumps(preview.payload,ensure_ascii=False,indent=2)
                token=signing.dumps({'actor':request.user.pk,'website':str(website.pk),'selection':preview.selection,'checksum':preview.checksum,'source_checksum':preview.source_checksum},salt='catalog-review',compress=True)
        except (ValidationError,signing.BadSignature,KeyError,ValueError,TypeError) as error:
            if not form.is_bound:
                form=ReviewForm({})
            form.is_valid()
            form.add_error(None,error.messages if isinstance(error,ValidationError) else 'This review expired or is invalid. Generate a new preview.')
            status=409
    return render(request,'portal/staff_review.html',context(request,artist,website=website,form=form,preview_json=preview_json,review_token=token,action_url=reverse('portal:review',args=[artist.pk,website.pk])),status=status)
