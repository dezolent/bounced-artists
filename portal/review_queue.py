"""Staff review inbox; decisions close issues without changing source/catalog data."""
from functools import wraps
from itertools import islice
import hashlib
import json
import re

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import CharField, Q, Value
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from artists.models import Artist, ArtistResourceGrant, Organization
from provenance.models import AuditEvent, FieldObservation, ReviewItem, SourceRecord
from .models import CatalogReviewRequest
from .services import StaleEdit


SALT='portal-review-queue'
TARGET_KINDS=('artist','recording','release','work','event','website')
# Source bodies are deliberately omitted. These fields are useful matching clues.
EVIDENCE_FIELDS={'title','name','version_title','release_title','artist_name','artist','artists','isrc','iswc','barcode','upc','release_date','duration_ms','recording_type','release_type','external_id','id','type','status'}
SENSITIVE=re.compile(r'(?i)(?:password|passwd|authorization|cookie|secret|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|private[_ -]?key|bearer\s+|://[^/\s]+:[^/\s]+@)')


def require_staff(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        raise PermissionDenied('Only active staff can review source issues and publication requests.')


def staff_required(view):
    @login_required
    @wraps(view)
    def guarded(request,*args,**kwargs):
        current=get_user_model().objects.filter(pk=request.user.pk).first()
        if current is None:
            raise PermissionDenied('Staff access was removed.')
        require_staff(current)
        request.user=current
        return view(request,*args,**kwargs)
    return guarded


def safe_text(value,limit=500):
    value=str(value)
    if SENSITIVE.search(value):
        return '[redacted sensitive content]'
    return value[:limit]+('… [truncated]' if len(value)>limit else '')


def evidence_value(value,depth=0):
    if depth>2:
        return '[nested content omitted]'
    if isinstance(value,dict):
        preview='; '.join(f'{safe_text(k,60)}: {evidence_value(v,depth+1)}' for k,v in islice(value.items(),8) if str(k).lower() in EVIDENCE_FIELDS) or '[structured content omitted or redacted]'
        return preview[:1500]+('… [truncated]' if len(preview)>1500 else '')
    if isinstance(value,list):
        preview='; '.join(evidence_value(child,depth+1) for child in value[:8])+(' [more omitted]' if len(value)>8 else '')
        return preview[:1500]+('… [truncated]' if len(preview)>1500 else '')
    return safe_text(value)


def issue_queryset():
    return ReviewItem.objects.select_related('source_record__organization','source_record__service','resolved_by').defer('source_record__payload_json')


def target_rows(issue):
    return issue.candidates.select_related('target__organization',*[f'target__{kind}' for kind in TARGET_KINDS]).order_by('pk')


def describe_target(target,organization_id):
    if target.organization_id!=organization_id:
        return {'label':'Invalid candidate: organization mismatch','url':'','invalid':True,'artists':[]}
    populated=[kind for kind in TARGET_KINDS if getattr(target,f'{kind}_id')]
    if len(populated)!=1:
        return {'label':'Invalid candidate target','url':'','invalid':True,'artists':[]}
    kind=populated[0];obj=getattr(target,kind)
    if obj.organization_id!=organization_id:
        return {'label':'Invalid candidate: organization mismatch','url':'','invalid':True,'artists':[]}
    label=safe_text(getattr(obj,'name',None) or getattr(obj,'title',None) or getattr(obj,'domain',None) or str(obj.pk),160)
    artists=Artist.objects.filter(organization_id=organization_id,archived_at__isnull=True,management_status='managed')
    if kind=='artist':
        artists=artists.filter(pk=obj.pk)
    elif kind=='website':
        artists=artists.filter(pk=obj.artist_id)
    else:
        artists=artists.filter(pk__in=ArtistResourceGrant.objects.filter(**{kind:obj}).values('artist_id'))
    artists=list(artists.order_by('name','pk')[:5])
    if artists and not getattr(obj,'archived_at',None):
        artist=artists[0]
        if kind=='artist':
            url=reverse('portal:artist',args=[artist.pk])
        elif kind in ('recording','release'):
            url=reverse(f'portal:{kind}',args=[artist.pk,obj.pk])
        elif kind=='website':
            url=reverse('portal:operation-detail',kwargs={'key':'publishing-website','object_id':str(obj.pk)})
        else:
            url=reverse('portal:detail',kwargs={'artist_id':artist.pk,'kind':{'work':'works','event':'events'}[kind],'object_id':str(obj.pk)})
    else:
        url=reverse('portal:operation-detail',kwargs={'key':f'{obj._meta.app_label}-{obj._meta.model_name}','object_id':str(obj.pk)})
    return {'label':label,'kind':kind,'url':url,'artists':artists,'invalid':False}


def candidate_summaries(issue,limit=5):
    return [describe_target(candidate.target,issue.source_record.organization_id) for candidate in target_rows(issue)[:limit]]


def issue_artist_query(artist):
    # Ownership follows explicit workspace grants, never credits or identifiers.
    query=Q(candidates__target__artist=artist)|Q(candidates__target__website__artist=artist)
    for kind in ('recording','release','work','event'):
        ids=ArtistResourceGrant.objects.filter(artist=artist).values(f'{kind}_id')
        query|=Q(**{f'candidates__target__{kind}_id__in':ids})
    return query & Q(source_record__organization=artist.organization,candidates__target__organization=artist.organization)


@staff_required
@require_GET
def review_queue(request):
    from .views import context
    query=request.GET.get('q','')[:200].strip()
    kind=request.GET.get('kind','all');status=request.GET.get('status','active')
    issues=issue_queryset();requests=CatalogReviewRequest.objects.select_related('artist__organization','requested_by')
    filter_error=None
    allowed_statuses=('active','all','open','pending','resolved','dismissed','returned')
    if kind not in ('all','issue','request') or status not in allowed_statuses:
        filter_error='Choose a valid review type and status.';issues=issues.none();requests=requests.none()
    if status=='active':
        issues=issues.filter(status='open');requests=requests.filter(status='pending')
    elif status!='all':
        issues=issues.filter(status=status);requests=requests.filter(status=status)
    if kind=='request':issues=issues.none()
    if kind=='issue':requests=requests.none()
    organization=request.GET.get('organization','')
    artist_id=request.GET.get('artist','')
    try:
        if organization:
            org=Organization.objects.get(pk=organization)
            issues=issues.filter(source_record__organization=org);requests=requests.filter(artist__organization=org)
        if artist_id:
            artist=Artist.objects.get(pk=artist_id)
            issues=issues.filter(issue_artist_query(artist));requests=requests.filter(artist=artist)
    except (Organization.DoesNotExist,Artist.DoesNotExist,ValidationError,ValueError):
        filter_error='Choose a valid organization or artist.';issues=issues.none();requests=requests.none()
    if query:
        issues=issues.filter(Q(issue_type__icontains=query)|Q(source_record__collector__icontains=query)|Q(source_record__object_type__icontains=query)|Q(source_record__external_id__icontains=query)|Q(source_record__organization__name__icontains=query)|Q(candidates__target__artist__name__icontains=query)|Q(candidates__target__recording__title__icontains=query)|Q(candidates__target__release__title__icontains=query)|Q(candidates__target__work__title__icontains=query)|Q(candidates__target__event__title__icontains=query)|Q(candidates__target__website__artist__name__icontains=query)|Q(candidates__target__recording__artist_grants__artist__name__icontains=query)|Q(candidates__target__release__artist_grants__artist__name__icontains=query)|Q(candidates__target__work__artist_grants__artist__name__icontains=query)|Q(candidates__target__event__artist_grants__artist__name__icontains=query))
        requests=requests.filter(Q(artist__name__icontains=query)|Q(artist__organization__name__icontains=query)|Q(requested_by__username__icontains=query))
    columns=('queue_id','queue_kind','created_at','status')
    issue_index=issues.order_by().annotate(queue_id=Cast('pk',CharField()),queue_kind=Value('issue',output_field=CharField())).values(*columns).distinct()
    request_index=requests.order_by().annotate(queue_id=Cast('pk',CharField()),queue_kind=Value('request',output_field=CharField())).values(*columns)
    page=Paginator(issue_index.union(request_index).order_by('-created_at','queue_kind','queue_id'),25).get_page(request.GET.get('page'))
    index=list(page.object_list)
    issue_map={str(row.pk):row for row in issue_queryset().filter(pk__in=[row['queue_id'] for row in index if row['queue_kind']=='issue'])}
    request_map={str(row.pk):row for row in CatalogReviewRequest.objects.select_related('artist__organization','requested_by').filter(pk__in=[row['queue_id'] for row in index if row['queue_kind']=='request'])}
    rows=[]
    for row in index:
        if row['queue_kind']=='issue':
            obj=issue_map[row['queue_id']]
            rows.append({'kind':'Source issue','title':safe_text(obj.issue_type.replace('_',' ').capitalize(),100),'status':obj.get_status_display(),'organization':obj.source_record.organization,'created_at':obj.created_at,'source':safe_text(obj.source_record.collector,100),'candidates':candidate_summaries(obj),'url':reverse('portal:review-issue',args=[obj.pk])})
        else:
            obj=request_map[row['queue_id']]
            rows.append({'kind':'Publication request','title':obj.artist.name,'status':obj.get_status_display(),'organization':obj.artist.organization,'created_at':obj.created_at,'source':f'Requested by {obj.requested_by.get_username()}' if obj.requested_by else 'Artist review requested','url':reverse('portal:review-request',args=[obj.pk])})
    params=request.GET.copy();params.pop('page',None)
    return render(request,'portal/review_queue.html',context(request,rows=rows,page_obj=page,query=query,kind=kind,status=status,filter_error=filter_error,organizations=Organization.objects.order_by('name'),filter_artists=Artist.objects.select_related('organization').order_by('name'),organization_filter=organization,artist_filter=artist_id,filter_query=params.urlencode(),status_options=[(v,v.capitalize()) for v in allowed_statuses]),status=400 if filter_error else 200)


class DecisionForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    action=forms.ChoiceField(choices=[])
    notes=forms.CharField(label='Decision notes',max_length=2000,widget=forms.Textarea(attrs={'rows':4}),help_text='Explain what was checked or what needs to change. Notes remain in the private audit history.')

    def __init__(self,*args,choices,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['action'].choices=choices
        if self.is_bound and set(self.data)-{'csrfmiddlewaretoken',*self.fields}:
            self.add_error(None,'Unexpected fields. Decisions cannot select or modify catalog candidates.')

    def clean_notes(self):
        value=self.cleaned_data['notes']
        if SENSITIVE.search(value):
            raise ValidationError('Remove credential or secret content from the decision notes.')
        return value


def choices_for(kind,status):
    if kind=='issue':
        return [('resolve','Mark resolved'),('dismiss','Dismiss issue')] if status=='open' else [('reopen','Reopen issue')] if status in ('resolved','dismissed') else []
    return [('return','Request changes')] if status=='pending' else [('reopen','Reopen request')] if status=='returned' else []


def snapshot(actor,kind,obj):
    if kind=='issue':
        candidates=[]
        for candidate in target_rows(obj):
            target=candidate.target
            entries=[(k,str(getattr(target,f'{k}_id')),str(getattr(getattr(target,k,None),'updated_at',''))) for k in TARGET_KINDS]
            candidates.append((str(candidate.pk),str(candidate.updated_at),str(target.updated_at),str(target.organization_id),entries))
        source=obj.source_record
        value={'item':str(obj.updated_at),'status':obj.status,'resolver':obj.resolved_by_id,'source':[str(source.pk),str(source.updated_at),source.payload_hash,source.payload_json],'candidates':candidates,'observations':list(FieldObservation.objects.filter(source_record=source).order_by('pk').values('id','updated_at','target_id','field_name','value_json','confidence'))}
    else:
        value={'status':obj.status,'created_at':str(obj.created_at),'artist':str(obj.artist_id),'artist_revision':str(obj.artist.updated_at),'requester':obj.requested_by_id,'website':str(obj.website_id),'selection':obj.publication_selection,'decision':str(AuditEvent.objects.filter(organization=obj.artist.organization,action='portal.review_request',change_json__review_request_id=str(obj.pk)).order_by('-created_at','pk').values_list('pk',flat=True).first())}
    digest=hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    return {'actor':actor.pk,'kind':kind,'id':str(obj.pk),'checksum':digest}


@transaction.atomic
def decide(actor,kind,object_id,data):
    if kind=='request':
        reference=get_object_or_404(CatalogReviewRequest,pk=object_id)
        Artist.objects.select_for_update().get(pk=reference.artist_id)
    current=get_user_model().objects.select_for_update().filter(pk=actor.pk).first()
    if current is None:
        raise PermissionDenied('Staff access was removed.')
    require_staff(current)
    if kind=='issue':
        # Lock the source before issue; evidence and issue decisions use the same source snapshot.
        reference=get_object_or_404(ReviewItem,pk=object_id)
        SourceRecord.objects.select_for_update().get(pk=reference.source_record_id)
        obj=get_object_or_404(ReviewItem.objects.select_for_update(),pk=object_id)
    else:
        obj=get_object_or_404(CatalogReviewRequest.objects.select_for_update(),pk=object_id)
    try:
        signed=signing.loads(data.get('version',''),salt=SALT,max_age=3600)
    except (signing.BadSignature,TypeError):
        raise StaleEdit('This review expired or is invalid. Reload before deciding.') from None
    if signed!=snapshot(current,kind,obj):
        raise StaleEdit('This issue, its evidence, candidate records or request changed. Reload and review the latest version.')
    form=DecisionForm(data,choices=choices_for(kind,obj.status))
    if not form.is_valid():
        return obj,form
    previous=obj.status;action=form.cleaned_data['action']
    if kind=='issue':
        invalid=target_rows(obj).exclude(target__organization_id=obj.source_record.organization_id).exists()
        for candidate in target_rows(obj):
            kinds=[k for k in TARGET_KINDS if getattr(candidate.target,f'{k}_id')]
            if len(kinds)!=1 or getattr(candidate.target,kinds[0]).organization_id!=obj.source_record.organization_id:
                invalid=True
        if invalid:
            form.add_error(None,'Fix the candidate organization mismatch or invalid target before deciding this issue.')
            return obj,form
        obj.status={'resolve':'resolved','dismiss':'dismissed','reopen':'open'}[action]
        obj.resolved_by=current if obj.status!='open' else None
        obj.full_clean();obj.save(update_fields=['status','resolved_by','updated_at'])
        organization=obj.source_record.organization
    else:
        desired={'return':'returned','reopen':'pending'}[action]
        if desired=='pending' and CatalogReviewRequest.objects.filter(artist_id=obj.artist_id,status='pending').exclude(pk=obj.pk).exists():
            form.add_error(None,'This artist already has a pending request. Open the current request instead.')
            return obj,form
        obj.status=desired
        obj.full_clean();obj.save(update_fields=['status'])
        organization=obj.artist.organization
    AuditEvent.objects.create(organization=organization,user=current,action=f'portal.review_{kind}',target_label=f'{"Source issue" if kind=="issue" else "Publication request"} {obj.pk}',change_json={f'review_{"item" if kind=="issue" else "request"}_id':str(obj.pk),'previous_status':previous,'status':obj.status,'decision':action,'notes':form.cleaned_data['notes']})
    return obj,None


def detail(request,kind,obj):
    from .views import context
    form=DecisionForm(request.POST if request.method=='POST' else None,choices=choices_for(kind,obj.status),initial={'version':signing.dumps(snapshot(request.user,kind,obj),salt=SALT)})
    status=200
    if request.method=='POST':
        try:
            obj,form=decide(request.user,kind,obj.pk,request.POST)
            if form is None:
                messages.success(request,'Review decision saved in the private audit history.')
                return redirect('portal:review-issue' if kind=='issue' else 'portal:review-request',**{'issue_id' if kind=='issue' else 'request_id':obj.pk})
            status=400
        except StaleEdit as error:
            form.is_valid();form.add_error(None,str(error));status=409
        except ValidationError as error:
            form.is_valid();form.add_error(None,error.messages);status=400
    values={'item':obj,'review_kind':kind,'form':form,'decisions_available':bool(choices_for(kind,obj.status))}
    history_key=f'review_{"item" if kind=="issue" else "request"}_id'
    organization=obj.source_record.organization if kind=='issue' else obj.artist.organization
    audits=AuditEvent.objects.filter(organization=organization,action=f'portal.review_{kind}',**{f'change_json__{history_key}':str(obj.pk)}).select_related('user').order_by('-created_at')[:30]
    values['history']=[{'actor':event.user.get_username() if event.user else 'Former account','created_at':event.created_at,'status':safe_text(event.change_json.get('status','')),'notes':safe_text(event.change_json.get('notes',''),2000)} for event in audits]
    if kind=='issue':
        source=obj.source_record
        payload=source.payload_json
        evidence=[{'label':key.replace('_',' ').capitalize(),'value':evidence_value(value)} for key,value in islice(payload.items(),100) if key.lower() in EVIDENCE_FIELDS] if isinstance(payload,dict) else []
        candidates=candidate_summaries(obj,21)
        observations=FieldObservation.objects.filter(source_record=source,target__organization=source.organization).select_related('target__organization',*[f'target__{field}' for field in TARGET_KINDS]).order_by('pk')[:21]
        values['issue_title']=safe_text(obj.issue_type.replace('_',' ').capitalize(),100)
        values.update(source_meta=[('Collector',safe_text(source.collector)),('Method',source.get_collection_method_display()),('Object type',safe_text(source.object_type)),('External ID',safe_text(source.external_id)),('Parser',safe_text(source.parser_version)),('Observed',source.observed_at),('Payload SHA-256',safe_text(source.payload_hash,64))],evidence=evidence,candidates=candidates[:20],candidates_omitted=len(candidates)>20,observations=[{'field':safe_text(row.field_name),'value':evidence_value(row.value_json),'confidence':row.confidence,'target':describe_target(row.target,source.organization_id)} for row in observations[:20]],observations_omitted=len(observations)>20,organization=organization)
    else:
        form.fields['notes'].help_text='This note is shown to the artist and saved in the private review history.'
        selection=obj.publication_selection
        if isinstance(selection,dict):
            values['publication_selection']=[{'label':label,'count':len(selection[key])} for key,label in [('recordings','Songs'),('releases','Releases'),('fanlinks','Fanlinks'),('artwork','Artwork'),('lyrics','Lyrics'),('works','Works')] if isinstance(selection.get(key),list)]
    return render(request,'portal/review_queue_detail.html',context(request,**values),status=status)


@staff_required
@require_http_methods(['GET','POST'])
def review_issue(request,issue_id):
    return detail(request,'issue',get_object_or_404(issue_queryset(),pk=issue_id))


@staff_required
@require_http_methods(['GET','POST'])
def review_request(request,request_id):
    return detail(request,'request',get_object_or_404(CatalogReviewRequest.objects.select_related('artist__organization','requested_by','website'),pk=request_id))
