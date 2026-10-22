"""Allowlisted private labels, metadata and search paths for editorial resources.

Apply these contracts only after access scoping. No source payloads, legal names,
credentials, storage locations or arbitrary model fields are exposed here.
"""
from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import Q

from .resources import RESOURCES


@dataclass(frozen=True)
class Presentation:
    labels: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...] = ()
    search: tuple[str, ...] = ()
    status: str = ''


PRESENTATIONS = {
    'labels': Presentation(('name',), (('Disambiguation','disambiguation'),('MBID','mbid')), ('name','disambiguation','mbid')),
    'release-groups': Presentation(('title',), (('Type','primary_type'),('First release','first_release_date'),('Disambiguation','disambiguation')), ('title','primary_type','disambiguation','mbid'), 'status'),
    'recording-labels': Presentation(('recording.title','label.name'), (('Position','position'),('Primary','is_primary')), ('recording__title','label__name')),
    'release-labels': Presentation(('release.title','label.name'), (('Catalog number','catalog_number'),('Position','position'),('Primary','is_primary')), ('release__title','label__name','catalog_number')),
    'release-genres': Presentation(('release.title','genre.name'), (), ('release__title','genre__name')),
    'release-group-genres': Presentation(('release_group.title','genre.name'), (), ('release_group__title','genre__name')),
    'media': Presentation(('release.title','title'), (('Disc','position'),('Format','format')), ('release__title','title','format')),
    'work-identifiers': Presentation(('work.title','value'), (('Scheme','scheme'),), ('work__title','scheme','value'), 'verification_status'),
    'artists': Presentation(('name',), (('Location','location'),('Timezone','timezone')), ('name','slug','location')),
    'recordings': Presentation(('title','version_title'), (('Type','recording_type'),('Language','language'),('Disambiguation','disambiguation'),('Release date','original_release_date')), ('title','version_title','recording_type','identifiers__value'), 'status'),
    'releases': Presentation(('title',), (('Type','release_type'),('Edition','edition'),('Release group','release_group.title'),('Catalog number','catalog_number'),('Country','country'),('Release date','release_date')), ('title','release_type','release_group__title','edition','catalog_number','identifiers__value'), 'status'),
    'assets': Presentation(('display_name',), (('Type','mime_type'),('Category','category'),('Visibility','visibility')), ('display_name','alt_text','mime_type','category')),
    'works': Presentation(('title',), (('ISWC','iswc'),('Type','work_type'),('Language','language'),('Disambiguation','disambiguation')), ('title','iswc','work_type','language','disambiguation','identifiers__value'), 'status'),
    'parties': Presentation(('display_name',), (('Type','party_type'),), ('display_name','party_type')),
    'party-identifiers': Presentation(('party.display_name','value'), (('Scheme','scheme'),), ('party__display_name','scheme','value')),
    'affiliations': Presentation(('party.display_name','society.name'), (('Reference','membership_reference'),('From','valid_from'),('Until','valid_to')), ('party__display_name','society__name','society__code','membership_reference')),
    'artist-parties': Presentation(('artist.name','party.display_name'), (('Role','relationship_type'),), ('artist__name','party__display_name','relationship_type')),
    'events': Presentation(('title',), (('Date','local_date'),('Venue','venue.name')), ('title','venue__name'), 'status'),
    'aliases': Presentation(('name',), (('Artist','artist.name'),('Type','alias_type')), ('name','artist__name','alias_type')),
    'contacts': Presentation(('label','name'), (('Artist','artist.name'),('Purpose','purpose'),('Value','value'),('Email','email'),('Visibility','visibility')), ('label','name','artist__name','purpose','value','email')),
    'profile-links': Presentation(('artist.name','platform.name'), (('URL','url'),('External ID','external_id')), ('artist__name','platform__name','url','external_id'), 'verification_status'),
    'artist-genres': Presentation(('artist.name','genre.name'), (), ('artist__name','genre__name')),
    'artist-assets': Presentation(('artist.name','asset.display_name'), (('Purpose','purpose'),('Order','sort_order')), ('artist__name','asset__display_name','purpose')),
    'recording-identifiers': Presentation(('recording.title','value'), (('Scheme','scheme'),), ('recording__title','scheme','value'), 'verification_status'),
    'release-identifiers': Presentation(('release.title','value'), (('Scheme','scheme'),), ('release__title','scheme','value')),
    'recording-credits': Presentation(('recording.title','artist.name'), (('Role','role'),('Billing order','billing_order'),('Credited as','credited_as'),('Join phrase','join_phrase')), ('recording__title','artist__name','role','credited_as')),
    'release-credits': Presentation(('release.title','artist.name'), (('Role','role'),('Billing order','billing_order'),('Credited as','credited_as'),('Join phrase','join_phrase')), ('release__title','artist__name','role','credited_as')),
    'recording-genres': Presentation(('recording.title','genre.name'), (), ('recording__title','genre__name')),
    'recording-relations': Presentation(('from_recording.title','to_recording.title'), (('Relationship','relation_type'),), ('from_recording__title','to_recording__title','relation_type')),
    'tracks': Presentation(('recording.title',), (('Release','release.title'),('Disc','disc_number'),('Track','track_number'),('Display title','display_title'),('Medium','medium.title'),('Credit','credit_text')), ('recording__title','release__title','display_title','credit_text')),
    'recording-assets': Presentation(('recording.title','asset.display_name'), (('Purpose','purpose'),('Primary','is_primary'),('Type','asset.mime_type')), ('recording__title','asset__display_name','purpose','asset__mime_type')),
    'release-assets': Presentation(('release.title','asset.display_name'), (('Purpose','purpose'),('Primary','is_primary'),('Type','asset.mime_type')), ('release__title','asset__display_name','purpose','asset__mime_type')),
    'recording-links': Presentation(('recording.title','platform_object.service.name'), (('Destination','platform_object.title'),('Relationship','relationship_type'),('URL','platform_object.url')), ('recording__title','platform_object__service__name','platform_object__title','platform_object__url','relationship_type'), 'review_status'),
    'release-links': Presentation(('release.title','platform_object.service.name'), (('Destination','platform_object.title'),('URL','platform_object.url')), ('release__title','platform_object__service__name','platform_object__title','platform_object__url'), 'review_status'),
    'fanlinks': Presentation(('title',), (('Song','recording.title'),('Release','release.title'),('Website','website.domain'),('Slug','slug')), ('title','recording__title','release__title','website__domain','slug'), 'status'),
    'destinations': Presentation(('fanlink_page.title','platform_object.service.name'), (('Label','label'),('Destination','platform_object.title'),('URL','platform_object.url'),('Order','sort_order'),('Enabled','enabled')), ('fanlink_page__title','platform_object__service__name','platform_object__title','platform_object__url','label')),
    'pages': Presentation(('title',), (('Website','website.domain'),('Path','path'),('Type','page_type')), ('title','website__domain','path','page_type'), 'publication_status'),
    'seo': Presentation(('page.title',), (('SEO title','title'),('Website','page.website.domain'),('Path','page.path'),('Canonical URL','canonical_url'),('Robots','robots')), ('page__title','title','page__website__domain','page__path','canonical_url')),
    'work-aliases': Presentation(('work.title','title'), (('Type','alias_type'),), ('work__title','title','alias_type')),
    'recording-works': Presentation(('recording.title','work.title'), (('Relationship','relationship_type'),), ('recording__title','work__title','relationship_type'), 'verification_status'),
    'contributors': Presentation(('work.title','party.display_name'), (('Role','role'),), ('work__title','party__display_name','role')),
    'registrations': Presentation(('work.title','society.name'), (('Work ID','external_work_id'),('Last verified','last_verified_at')), ('work__title','society__name','society__code','external_work_id'), 'registration_status'),
    'claims': Presentation(('registration.work.title','party.display_name'), (('Society','registration.society.name'),('Right','right_type'),('Share','share_value'),('Basis','share_basis'),('Territory','territory'),('From','valid_from'),('Until','valid_to')), ('registration__work__title','registration__society__name','party__display_name','right_type','share_basis','territory')),
    'event-artists': Presentation(('event.title','artist.name'), (('Role','performance_role'),('Billing order','billing_order')), ('event__title','artist__name','performance_role')),
    'tickets': Presentation(('event.title',), (('Type','offer_type'),('URL','url')), ('event__title','offer_type','url'), 'status'),
}

# Foreign keys offered by forms also include reference entities outside RESOURCES.
CHOICE_PRESENTATIONS = {
    'labels': Presentation(('name',), (('Disambiguation','disambiguation'),('MBID','mbid')), ('name','disambiguation','mbid')),
    'release-groups': Presentation(('title',), (('Type','primary_type'),('First release','first_release_date'),('Disambiguation','disambiguation')), ('title','primary_type','disambiguation','mbid'), 'status'),
    'recording-labels': Presentation(('recording.title','label.name'), (('Position','position'),('Primary','is_primary')), ('recording__title','label__name')),
    'release-labels': Presentation(('release.title','label.name'), (('Catalog number','catalog_number'),('Position','position'),('Primary','is_primary')), ('release__title','label__name','catalog_number')),
    'release-genres': Presentation(('release.title','genre.name'), (), ('release__title','genre__name')),
    'release-group-genres': Presentation(('release_group.title','genre.name'), (), ('release_group__title','genre__name')),
    'media': Presentation(('release.title','title'), (('Disc','position'),('Format','format')), ('release__title','title','format')),
    'work-identifiers': Presentation(('work.title','value'), (('Scheme','scheme'),), ('work__title','scheme','value'), 'verification_status'),
    'artists.service': Presentation(('name',)),
    'catalog.genre': Presentation(('name',)),
    'rights.rightssociety': Presentation(('name','code')),
    'publishing.website': Presentation(('domain',)),
    'catalog.platformobject': Presentation(('service.name','title'), (('URL','url'),)),
}


def _contract(model, kind=None):
    if kind is not None:
        return PRESENTATIONS[kind]
    for name,spec in RESOURCES.items():
        if spec.model is model:
            return PRESENTATIONS[name]
    return CHOICE_PRESENTATIONS.get(model._meta.label_lower)


HUMAN_WORD_FIELDS = {'role','relationship_type','relation_type','party_type','alias_type','purpose','recording_type','release_type','performance_role','right_type','page_type','offer_type','share_basis','primary_type'}


def _value(obj, path):
    pieces=path.split('.')
    for name in pieces[:-1]:
        obj=getattr(obj,name,None)
        if obj is None:
            return ''
    name=pieces[-1]
    value=getattr(obj,name,None)
    if value is None or value == '':
        return ''
    display=getattr(obj,f'get_{name}_display',None)
    if callable(display):
        return str(display())
    if isinstance(value,bool):
        return 'Yes' if value else 'No'
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    # Values are scalar paths declared above, never arbitrary object __str__.
    return str(value).replace('_',' ') if name in HUMAN_WORD_FIELDS else str(value)


def present(obj,kind=None):
    contract=_contract(type(obj),kind)
    fallback=f'{obj._meta.verbose_name.capitalize()} {str(obj.pk)[:8]}'
    if contract is None:
        return {'label':fallback,'subtitle':'','metadata':[],'status':''}
    values=[_value(obj,path) for path in contract.labels]
    metadata=[{'name':name,'value':value} for name,path in contract.metadata if (value:=_value(obj,path))]
    return {'label':' · '.join(value for value in values if value) or fallback,'subtitle':' · '.join(f"{row['name']}: {row['value']}" for row in metadata),'metadata':metadata,'status':str(getattr(obj,contract.status,'')) if contract.status else ''}


def entity_label(obj):
    row=present(obj)
    return f"{row['label']} — {row['subtitle']}" if row['subtitle'] else row['label']


def prepare_queryset(queryset,kind=None):
    """Eager load only the forward relations used by this display contract."""
    contract=_contract(queryset.model,kind)
    if contract is None:
        return queryset
    related=set()
    for path in (*contract.labels,*(path for _,path in contract.metadata)):
        pieces=path.split('.')
        if len(pieces)>1:
            related.add('__'.join(pieces[:-1]))
    return queryset.select_related(*sorted(related)) if related else queryset


def search_queryset(queryset,kind,query):
    """Search the declared human names/metadata within an already scoped set."""
    if not query:
        return queryset
    predicates=Q()
    for path in PRESENTATIONS[kind].search:
        predicates|=Q(**{f'{path}__icontains':query})
        if path.split('__')[-1] in HUMAN_WORD_FIELDS and ' ' in query:
            predicates|=Q(**{f'{path}__icontains':query.replace(' ','_')})
    return queryset.filter(predicates).distinct() if predicates else queryset.none()
