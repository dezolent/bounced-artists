"""A scoped, transactional editor for a catalog item and its relationships.

Legacy resource endpoints remain useful, but artists need only this form. Child
UUIDs are resolved from the current item's scoped rows, never from POST globally.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.forms import formset_factory, modelform_factory
from django.utils.text import slugify
from django.utils.functional import cached_property

from artists.models import Artist, ArtistMembership
from catalog import models as c
from rights import models as r
from .access import grant_created, require_editor
from .asset_display import TEXT_LIMIT, text_preview
from .forms import make_form, stamp
from .media import prepare_upload
from .presentation import entity_label, prepare_queryset
from .querysets import choices, scoped
from .resources import resource
from .services import StaleEdit, mutate, remove


@dataclass(frozen=True)
class Part:
    prefix: str
    title: str
    kind: str
    parent_field: str


def parts(kind):
    parent = 'recording' if kind == 'recordings' else 'release'
    stem = 'recording' if parent == 'recording' else 'release'
    result = [Part('credits', 'Artists & collaborators', f'{stem}-credits', parent),
              Part('identifiers', 'Identifiers', f'{stem}-identifiers', parent),
              Part('genres', 'Genres', f'{stem}-genres', parent),
              Part('labels', 'Record labels', f'{stem}-labels', parent),
              Part('assets', 'Artwork, lyrics & files', f'{stem}-assets', parent),
              Part('links', 'Platform links', f'{stem}-links', parent),
              Part('fanlinks', 'Fanlinks', 'fanlinks', parent)]
    if parent == 'recording':
        result += [Part('works', 'Related works', 'recording-works', parent),
                   Part('appearances', 'Appears on', 'tracks', parent),
                   Part('relations', 'Related recordings', 'recording-relations', 'from_recording')]
    else:
        result += [Part('media', 'Discs / media', 'media', parent),
                   Part('tracks', 'Tracklist', 'tracks', parent)]
    return result


def row_data(form):
    return {name: value.pk if hasattr(value, 'pk') else value
            for name, value in form.cleaned_data.items()
            if name not in ('row_id', 'DELETE') and not name.startswith('new_')}


class Section:
    def __init__(self, editor, part, parent, *, prefix=None):
        self.editor, self.part, self.parent = editor, part, parent
        self.prefix, self.title = prefix or part.prefix, part.title
        self.kind = part.kind
        spec = resource(part.kind)
        self.rows = list(scoped(editor.user, editor.artist, part.kind).filter(**{part.parent_field: parent}).order_by('pk')) if parent.pk and not parent._state.adding else []
        self.by_id = {str(obj.pk): obj for obj in self.rows}
        self.original_positions = {str(obj.pk):(getattr(obj,'release_id',None),getattr(obj,'disc_number',None),getattr(obj,'track_number',None),getattr(obj,'position',None)) for obj in self.rows}
        self.write_ids = set(str(pk) for pk in scoped(editor.user, editor.artist, part.kind, write=True).filter(pk__in=[obj.pk for obj in self.rows]).values_list('pk', flat=True))
        self.form_class = self._form_class(spec)
        factory = formset_factory(self.form_class, extra=0, can_delete=True, max_num=150, absolute_max=200, validate_max=True)
        self.formset = factory(data=editor.data, files=editor.files, prefix=self.prefix,
                               initial=[self.initial(obj) for obj in self.rows])
        for form in self.formset:
            self.configure(form)

    def initial(self, obj):
        initial = {name: getattr(obj, name) for name in self.form_class.base_fields if hasattr(obj, name)}
        initial.update(row_id=obj.pk, version=stamp(obj))
        if self.kind.endswith('-links'):
            initial.update(service=obj.platform_object.service_id, url=obj.platform_object.url)
        if self.kind.endswith('-assets'):
            asset = obj.asset
            for name in ('display_name','category','description','alt_text','visibility','rights_basis'):
                initial[name] = getattr(asset, name)
            preview = text_preview(asset)
            if preview and not preview['unavailable'] and not preview['truncated']:
                initial['text_content'] = preview['text']
            initial['asset_version'] = stamp(asset)
        if self.kind == 'recording-works':
            for name in resource('works').fields:
                initial['work_' + name] = getattr(obj.work, name)
            initial['work_version'] = stamp(obj.work)
        return initial

    def _form_class(self, spec):
        # Parent pointers are fixed by this editor, not selectable by the browser.
        fields = tuple(name for name in spec.fields if name != self.part.parent_field and not (self.kind == 'fanlinks' and name in ('recording','release')))
        base = modelform_factory(spec.model, fields=fields)
        section = self

        class Row(base):
            row_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
            version = forms.CharField(required=False, widget=forms.HiddenInput)

            @cached_property
            def changed_data(self):
                # TimestampSigner renews tokens on every GET/POST. Tokens guard
                # concurrency but never mean the catalog data itself changed.
                return [name for name in super().changed_data if name not in {'row_id','version','asset_version','work_version'}]

            def _get_validation_exclusions(self):
                excluded = super()._get_validation_exclusions()
                if section.kind == 'tracks':
                    # Final positions are checked across the coordinated form;
                    # existing rows can occupy each other's intended slots.
                    excluded.add('release')
                return excluded

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                raw_id = self.data.get(self.add_prefix('row_id')) if self.is_bound else self.initial.get('row_id')
                obj = section.by_id.get(str(raw_id)) if raw_id else None
                self.catalog_object = obj
                self.original_work_id = obj.work_id if obj and section.kind=='recording-works' else None
                self.read_only = bool(obj and str(obj.pk) not in section.write_ids)
                if obj:
                    self.instance = obj
                setattr(self.instance, section.part.parent_field, section.parent)
                for name, field in self.fields.items():
                    if isinstance(field, forms.ModelChoiceField):
                        field.queryset = prepare_queryset(choices(section.editor.user, section.editor.artist, field.queryset.model, write=not self.read_only))
                        field.label_from_instance = entity_label
                    if name == 'join_phrase':
                        field.strip = False
                if self.read_only:
                    for field in self.fields.values():
                        field.disabled = True

            def clean(self):
                cleaned = super().clean()
                raw = self.data.get(self.add_prefix('row_id')) if self.is_bound else None
                if raw and str(raw) not in section.by_id:
                    raise ValidationError('This related record is not part of the item you can edit.')
                if self.read_only:
                    return cleaned
                from .validation import safe_external_url
                if cleaned.get('url'):
                    safe_external_url(cleaned['url'])
                for field, new_field in (('artist','new_artist'),('genre','new_genre'),('label','new_label'),('recording','new_recording'),('party','new_party')):
                    if new_field not in self.fields:
                        continue
                    if cleaned.get(field) and cleaned.get(new_field):
                        self.add_error(new_field, 'Choose an existing item or enter a new name, not both.')
                    if not cleaned.get(field) and not cleaned.get(new_field):
                        self.add_error(field, 'Choose an existing item or enter a new name.')
                if section.kind.endswith('-assets'):
                    if not cleaned.get('asset') and not cleaned.get('upload') and not cleaned.get('text_content'):
                        self.add_error('asset', 'Select an asset, upload a file or enter text.')
                    if cleaned.get('upload') and cleaned.get('text_content'):
                        self.add_error('upload', 'Use a file upload or edit text in this row, not both.')
                    asset = cleaned.get('asset')
                    if asset and not scoped(section.editor.user, section.editor.artist, 'assets', write=True).filter(pk=asset.pk).exists():
                        self.add_error('asset', 'You need edit access to the selected asset.')
                if section.kind == 'recording-works' and not cleaned.get('work') and not cleaned.get('work_title'):
                    self.add_error('work_title', 'Choose a work or enter a composition title.')
                if section.kind == 'destinations' and not cleaned.get('platform_object') and not (cleaned.get('service') and cleaned.get('url')):
                    self.add_error('url', 'Choose a platform destination or enter its service and URL.')
                return cleaned

        if self.kind.endswith('-links'):
            from artists.models import Service
            Row.base_fields.update(service=forms.ModelChoiceField(queryset=Service.objects.all()), url=forms.URLField(max_length=2048))
        if self.kind == 'destinations':
            from artists.models import Service
            Row.base_fields['platform_object'].required = False
            Row.base_fields.update(service=forms.ModelChoiceField(queryset=Service.objects.all(), required=False), url=forms.URLField(max_length=2048, required=False))
        for field, new_field, label in (('artist','new_artist','New collaborator name'),('genre','new_genre','New genre name'),('label','new_label','New label name'),('recording','new_recording','New song title'),('party','new_party','New contributor name')):
            if isinstance(Row.base_fields.get(field),forms.ModelChoiceField):
                Row.base_fields[field].required = False
                max_length = Artist._meta.get_field('name').max_length if field == 'artist' else 100 if field == 'genre' else 300
                Row.base_fields[new_field] = forms.CharField(label=label, max_length=max_length, required=False)
        if self.kind.endswith('-assets'):
            Row.base_fields['asset'].required = False
            roles = [('cover','Cover artwork'),('lyrics','Lyrics'),('audio','Audio'),('video','Video'),('document','Document'),('other','Other file')]
            known = {value for value,_ in roles}
            roles += [(obj.purpose,obj.purpose.replace('_',' ').capitalize()) for obj in self.rows if obj.purpose not in known]
            Row.base_fields['purpose'] = forms.ChoiceField(label='File role',choices=roles)
            Row.base_fields['is_primary'].label = 'Primary file for this role'
            Row.base_fields['is_primary'].help_text = 'The primary cover is shown on the song or release. Other variants remain attached.'
            for name in ('display_name','category','description','alt_text','visibility','rights_basis'):
                field = c.Asset._meta.get_field(name).formfield()
                field.required = False
                Row.base_fields[name] = field
            Row.base_fields.update(upload=forms.FileField(required=False),
                                  text_content=forms.CharField(label='Text / lyrics contents', required=False, strip=False, max_length=TEXT_LIMIT, widget=forms.Textarea(attrs={'rows':8})),
                                  asset_version=forms.CharField(required=False, widget=forms.HiddenInput))
        if self.kind == 'recording-works':
            Row.base_fields['work'].required = False
            for name in resource('works').fields:
                field = r.MusicalWork._meta.get_field(name).formfield()
                field.required = False
                field.label = 'Composition ' + name.replace('_',' ')
                Row.base_fields['work_'+name] = field
            Row.base_fields['work_version'] = forms.CharField(required=False, widget=forms.HiddenInput)
        return Row

    def configure(self, form):
        obj = form.catalog_object
        form.nested_sections = []
        if self.kind == 'fanlinks':
            parent = obj or resource('fanlinks').model()
            form.nested_sections.append(Section(self.editor, Part('destinations','Destinations','destinations','fanlink_page'), parent, prefix=form.prefix+'-destinations'))
        if self.kind == 'recording-works':
            parent = obj.work if obj else r.MusicalWork(organization=self.editor.artist.organization)
            form.nested_sections += [Section(self.editor, Part('contributors','Writers & contributors','contributors','work'), parent, prefix=form.prefix+'-contributors'),
                                     Section(self.editor, Part('work-identifiers','Composition identifiers','work-identifiers','work'), parent, prefix=form.prefix+'-work-identifiers')]
        if self.kind.endswith('-assets') and obj:
            form.preview = text_preview(obj.asset)
            form.asset_name = entity_label(obj.asset)
            if obj.asset.current_version_id and obj.asset.current_version.preview_file:
                from django.urls import reverse
                form.asset_cover_url = reverse('portal:asset-media',args=[self.editor.artist.pk,obj.asset_id,'preview'])

    @property
    def empty_form(self):
        form = self.formset.empty_form
        self.configure(form)
        return form

    def is_valid(self):
        valid = self.formset.is_valid()
        seen = set()
        submitted_ids = set()
        for form in self.formset:
            cleaned = getattr(form, 'cleaned_data', {})
            row_id = str(cleaned.get('row_id') or '')
            if row_id:
                if row_id in submitted_ids:
                    form.add_error(None, 'A related record was submitted more than once.')
                    valid = False
                submitted_ids.add(row_id)
            if cleaned.get('DELETE') or form.read_only:
                continue
            for nested in form.nested_sections:
                valid = nested.is_valid() and valid
            if self.kind == 'recording-works' and form.original_work_id:
                selected = cleaned.get('work')
                if selected is None or selected.pk != form.original_work_id:
                    edited_work = any(name.startswith('work_') for name in form.changed_data)
                    edited_children = any(any(child.has_changed() for child in nested.formset) for nested in form.nested_sections)
                    if edited_work or edited_children:
                        form.add_error('work','Save the work selection first, then reload before editing its composition details or contributors.')
                        valid = False
            if not form.has_changed():
                continue
            if form.errors:
                valid = False
            key_fields = {
                'credits': ('artist','new_artist','role'), 'identifiers': ('scheme','value'),
                'genres': ('genre','new_genre'), 'labels': ('label','new_label'),
                'links': ('service','url','relationship_type'), 'media': ('position',),
                'tracks': ('disc_number','track_number'), 'appearances': ('release','disc_number','track_number'),
            }.get(self.part.prefix)
            if key_fields and not form.errors:
                key = tuple(str(cleaned.get(name) or '') for name in key_fields)
                if key in seen:
                    form.add_error(None, 'This duplicates another row in this section.')
                    valid = False
                seen.add(key)
        # Omitting an existing row cannot act as an unaudited deletion.
        if set(self.by_id) - submitted_ids:
            self.formset._non_form_errors = self.formset.error_class(['Related records are missing from this submission. Reload before saving.'])
            valid = False
        return valid


class CatalogEditor:
    def __init__(self, user, artist, kind, *, instance=None, data=None, files=None):
        self.user, self.artist, self.kind = user, artist, kind
        self.instance = instance or resource(kind).model(organization=artist.organization)
        self.data, self.files = data, files
        self.form = make_form(kind, user, artist, data=data, files=files, instance=instance)
        self.sections = [Section(self, part, self.instance) for part in parts(kind)]
        self.form.fields['catalog_version'] = forms.CharField(widget=forms.HiddenInput, initial=self.graph_version())
        self.form.fields['new_release_group'] = forms.CharField(required=False, max_length=300, label='New release group title') if kind == 'releases' else forms.CharField(required=False, widget=forms.HiddenInput)
        if kind == 'recordings':
            from .catalog_display import duration
            self.form.fields['duration_ms'].widget = forms.HiddenInput()
            self.form.fields['duration'] = forms.CharField(required=False,label='Duration',initial=duration(self.instance.duration_ms) if self.instance.duration_ms is not None else '',help_text='Minutes:seconds, or hours:minutes:seconds.')
        self.stored_files = []
        self.demoted = set()

    def graph_version(self):
        records = [self.instance] if not self.instance._state.adding else []
        for section in self.sections:
            for obj in section.rows:
                records.append(obj)
                if section.kind.endswith('-assets'):
                    records.append(obj.asset)
                if section.kind == 'recording-works':
                    records.append(obj.work)
                    records.extend(scoped(self.user,self.artist,'contributors').filter(work=obj.work))
                    records.extend(scoped(self.user,self.artist,'work-identifiers').filter(work=obj.work))
                if section.kind == 'fanlinks':
                    records.extend(scoped(self.user,self.artist,'destinations').filter(fanlink_page=obj))
        value = sorted((obj._meta.label_lower,str(obj.pk),str(obj.updated_at),getattr(obj,'revision',None)) for obj in records)
        digest = hashlib.sha256(json.dumps(value).encode()).hexdigest()
        return signing.dumps(digest, salt='catalog-graph')

    def is_valid(self):
        valid = self.form.is_valid()
        if self.kind == 'recordings':
            value = self.form.cleaned_data.get('duration','')
            if 'duration' not in self.form.changed_data:
                self.form.cleaned_data['duration_ms'] = self.instance.duration_ms
            elif value:
                if not re.fullmatch(r'\d+:[0-5]\d(?::[0-5]\d)?',value):
                    self.form.add_error('duration','Enter minutes:seconds or hours:minutes:seconds.')
                    valid = False
                else:
                    components = [int(piece) for piece in value.split(':')]
                    milliseconds = sum(piece*(60**index) for index,piece in enumerate(reversed(components)))*1000
                    if milliseconds > 2147483647:
                        self.form.add_error('duration','Duration is too long.')
                        valid = False
                    else:
                        self.form.cleaned_data['duration_ms'] = milliseconds
            else:
                self.form.cleaned_data['duration_ms'] = None
        if self.form.cleaned_data.get('new_release_group') and self.form.cleaned_data.get('release_group'):
            self.form.add_error('new_release_group', 'Choose a release group or enter a new title, not both.')
            valid = False
        for section in self.sections:
            valid = section.is_valid() and valid
        return valid

    def _new_reference(self, field, name):
        if field == 'artist':
            stem = slugify(name)[:180] or 'collaborator'
            slug = stem
            index = 1
            while Artist.objects.filter(slug=slug).exists():
                index += 1
                slug = f'{stem}-{index}'
            return Artist.objects.create(organization=self.artist.organization, name=name, slug=slug)
        if field == 'genre':
            # Genres are shared reference data. Never rename an existing genre.
            stem = slugify(name)[:90] or 'genre'
            existing = c.Genre.objects.filter(name__iexact=name).first()
            if existing:
                return existing
            slug, index = stem, 1
            while c.Genre.objects.filter(slug=slug).exists():
                index += 1
                slug = f'{stem}-{index}'
            return c.Genre.objects.create(name=name, slug=slug)
        kind = {'label':'labels','recording':'recordings','party':'parties'}[field]
        data = {'label':{'name':name},'recording':{'title':name,'recording_type':'original','date_precision':'unknown'},'party':{'display_name':name,'party_type':'person'}}[field]
        obj, form = mutate(self.user,self.artist,kind,data)
        if obj is None:
            raise ValidationError(form.errors.as_text())
        return obj

    def _save_asset(self, form, values):
        asset = form.cleaned_data.get('asset')
        upload = form.cleaned_data.get('upload')
        content = form.cleaned_data.get('text_content')
        metadata = {name:form.cleaned_data.get(name) or '' for name in ('display_name','category','description','alt_text','rights_basis')}
        metadata['visibility'] = form.cleaned_data.get('visibility') or (asset.visibility if asset else 'members')
        changed = set(form.changed_data)
        if asset:
            for name in metadata:
                if name not in changed:
                    metadata[name] = getattr(asset,name)
        content_changed = 'text_content' in changed
        if asset and content_changed and not upload:
            preview = text_preview(asset)
            if not preview or preview['truncated'] or preview['unavailable']:
                raise ValidationError('This file cannot be edited as bounded text. Upload a replacement file instead.')
        if content_changed and (content or asset):
            name = asset.current_version.original_name if asset and asset.current_version_id else 'lyrics.txt'
            upload = SimpleUploadedFile(name, (content or '').encode('utf-8'), content_type='text/plain')
        metadata['version'] = form.cleaned_data.get('asset_version')
        if not metadata['display_name'] and not asset:
            metadata['display_name'] = getattr(upload,'name','Lyrics')
        if not asset or upload or changed & set(metadata):
            obj, asset_form = mutate(self.user,self.artist,'assets',metadata,{'upload':upload} if upload else {},asset.pk if asset else None)
            if obj is None:
                raise ValidationError(asset_form.errors.as_text())
            asset = obj
            if upload and asset.current_version_id:
                self.stored_files += [field for field in (asset.current_version.file,asset.current_version.preview_file) if field]
        values['asset'] = asset.pk

    def _save_section(self, section, parent):
        # Removal frees an active track position before another row reuses it.
        for form in section.formset:
            if form.cleaned_data.get('DELETE') and form.catalog_object and not form.read_only:
                remove(self.user,self.artist,section.kind,form.catalog_object.pk,form.cleaned_data.get('version'))
        for form in section.formset:
            cleaned = form.cleaned_data
            obj = form.catalog_object
            if form.read_only:
                continue
            nested_changed = any(any(f.has_changed() for f in nested.formset) for nested in form.nested_sections)
            if not form.has_changed() and not nested_changed:
                continue
            if cleaned.get('DELETE'):
                continue
            values = row_data(form)
            values[section.part.parent_field] = parent.pk
            for field in ('artist','genre','label','recording','party'):
                name = cleaned.get('new_'+field)
                if name:
                    values[field] = self._new_reference(field,name).pk
            if section.kind.endswith('-assets'):
                self._save_asset(form,values)
                # Updating a cover must release the previous primary slot first.
                if cleaned.get('is_primary'):
                    peers = scoped(self.user,self.artist,section.kind,write=True,include_removed=True).filter(**{section.part.parent_field:parent,'purpose':cleaned.get('purpose'),'is_primary':True})
                    for peer in peers.exclude(pk=obj.pk if obj else None):
                        self._demote_primary(peer)
            if section.kind == 'destinations' and cleaned.get('service') and cleaned.get('url'):
                platform, _ = c.PlatformObject.objects.get_or_create(service=cleaned['service'],object_type='recording' if self.kind=='recordings' else 'release',external_id='',url=cleaned['url'])
                values['platform_object'] = platform.pk
            if section.kind.endswith('-labels') and cleaned.get('is_primary'):
                for peer in scoped(self.user,self.artist,section.kind,write=True,include_removed=True).filter(**{section.part.parent_field:parent,'is_primary':True}).exclude(pk=obj.pk if obj else None):
                    self._demote_primary(peer)
            if section.kind == 'tracks' and not values.get('medium'):
                release_id = values.get('release')
                medium = scoped(self.user,self.artist,'media',write=True).filter(release_id=release_id,position=cleaned.get('disc_number')).first()
                if medium:
                    values['medium'] = medium.pk
            if section.kind == 'recording-works':
                work = cleaned.get('work')
                work_values = {name:cleaned.get('work_'+name) or '' for name in resource('works').fields}
                if not work or any('work_'+name in form.changed_data for name in resource('works').fields):
                    work_values['version'] = cleaned.get('work_version')
                    work, work_form = mutate(self.user,self.artist,'works',work_values,object_id=work.pk if work else None)
                    if work is None:
                        raise ValidationError(work_form.errors.as_text())
                values['work'] = work.pk
            else:
                work = None
            if obj and not form.has_changed():
                result = obj
            else:
                result, child_form = mutate(self.user,self.artist,section.kind,values,object_id=obj.pk if obj else None)
                if result is None:
                    raise ValidationError(child_form.errors.as_text())
            for nested in form.nested_sections:
                self._save_section(nested,work or result)

    def _demote_primary(self, peer):
        from .models import EditorialChange
        # Keep the signed revision usable for any explicit row edit later in the
        # same transaction; the automatic change gets its own audit entry.
        type(peer).objects.filter(pk=peer.pk).update(is_primary=False)
        self.demoted.add((type(peer),peer.pk))
        EditorialChange.objects.create(organization=self.artist.organization,artist=self.artist,user=self.user,
                                       model_label=peer._meta.label_lower,object_id=str(peer.pk),action='update',changed_fields=['is_primary'])

    def _stage_track_positions(self):
        """Reserve free positions to permit swaps under immediate unique checks.

        Only rows actually changing position move. Their updated_at remains the
        original value until mutate validates the signed revision and audits it.
        All temporary changes roll back with the enclosing transaction.
        """
        for section in self.sections:
            if section.kind != 'tracks':
                continue
            for form in section.formset:
                obj = form.catalog_object
                if not obj or form.read_only or form.cleaned_data.get('DELETE'):
                    continue
                old_release,old_disc,old_track,_ = section.original_positions[str(obj.pk)]
                target_release = form.cleaned_data.get('release') if section.part.parent_field == 'recording' else self.instance
                if (old_release,old_disc,old_track) == (target_release.pk,form.cleaned_data.get('disc_number'),form.cleaned_data.get('track_number')):
                    continue
                used = set(c.ReleaseTrack.objects.filter(release_id=old_release,disc_number=old_disc,archived_at__isnull=True).values_list('track_number',flat=True))
                slot = next((number for number in range(32767,32000,-1) if number not in used),None)
                if slot is None:
                    raise ValidationError('There are too many tracks to reorder in this disc.')
                c.ReleaseTrack.objects.filter(pk=obj.pk).update(track_number=slot)

    def save(self):
        try:
            with transaction.atomic():
                locked_artist = Artist.objects.select_for_update().get(pk=self.artist.pk,archived_at__isnull=True)
                if not self.user.is_staff:
                    if not ArtistMembership.objects.select_for_update().filter(artist=locked_artist,user=self.user).exists():
                        raise PermissionDenied('Artist access was removed.')
                    list(locked_artist.resource_grants.select_for_update())
                require_editor(self.user,locked_artist)
                fresh = CatalogEditor(self.user,locked_artist,self.kind,instance=resource(self.kind).model.objects.get(pk=self.instance.pk) if not self.instance._state.adding else None)
                try:
                    submitted = signing.loads(self.data.get('catalog_version'),salt='catalog-graph',max_age=86400)
                    current = signing.loads(fresh.graph_version(),salt='catalog-graph')
                except (signing.BadSignature,TypeError):
                    raise StaleEdit('Reload this catalog form before saving.')
                if submitted != current:
                    raise StaleEdit('This item or its related data changed since you opened it. Reload to review the latest version.')
                if not self.is_valid():
                    return None
                # Validate new upload bytes before any data mutations.
                total = 0
                for section in self.sections:
                    for form in section.formset:
                        if form.cleaned_data.get('DELETE'):
                            continue
                        upload = form.cleaned_data.get('upload')
                        if upload:
                            total += upload.size
                            prepare_upload(upload)
                if total > 250*1024*1024:
                    raise ValidationError('Combined uploads must be 250 MiB or smaller.')
                values = row_data(self.form)
                if self.kind == 'releases' and self.form.cleaned_data.get('new_release_group'):
                    group, group_form = mutate(self.user,self.artist,'release-groups',{'title':self.form.cleaned_data['new_release_group'],'primary_type':values.get('release_type') or 'album','date_precision':'unknown'})
                    if group is None:
                        raise ValidationError(group_form.errors.as_text())
                    values['release_group'] = group.pk
                obj, base_form = mutate(self.user,self.artist,self.kind,values,object_id=self.instance.pk if not self.instance._state.adding else None)
                if obj is None:
                    raise ValidationError(base_form.errors.as_text())
                self._stage_track_positions()
                for section in self.sections:
                    self._save_section(section,obj)
                from django.utils import timezone
                for model,pk in self.demoted:
                    model.objects.filter(pk=pk).update(updated_at=timezone.now())
                return obj
        except Exception:
            for field in self.stored_files:
                field.storage.delete(field.name)
            raise
