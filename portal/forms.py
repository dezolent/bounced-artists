from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from artists.models import Service
from .resources import resource
from .querysets import choices
from .validation import safe_external_url


def stamp(obj):
    return signing.dumps([obj._meta.label_lower, str(obj.pk), str(obj.updated_at), getattr(obj, 'revision', None)], salt='portal-edit')


def make_form(kind, user, artist, *, data=None, files=None, instance=None,read_only=False):
    spec = resource(kind)
    extras = {'version': forms.CharField(widget=forms.HiddenInput, required=False)}
    if kind == 'assets':
        extras['upload'] = forms.FileField(required=instance is None, help_text='Images up to 20 MiB, audio up to 250 MiB, PDF/TXT/CSV up to 25 MiB. Files remain private until reviewed.')
    if kind in {'recording-links','release-links'}:
        extras['service'] = forms.ModelChoiceField(queryset=Service.objects.all())
        extras['url'] = forms.URLField(max_length=2048)
    if kind == 'pages':
        extras['editorial_text'] = forms.CharField(widget=forms.Textarea, required=False, help_text='Editorial text. Original imported content is preserved separately.')
    base = modelform_factory(spec.model, fields=spec.fields)

    class ScopedForm(base):
        def clean(self):
            cleaned = super().clean()
            for name in ('url','canonical_url'):
                if cleaned.get(name):
                    try:
                        safe_external_url(cleaned[name])
                    except ValidationError as error:
                        self.add_error(name, error)
            if kind == 'contacts' and not user.is_staff and cleaned.get('visibility') == 'private':
                self.add_error('visibility', 'Only staff can manage staff-only contacts.')
            return cleaned

    ScopedForm.base_fields.update(extras)
    obj = instance or spec.model()
    if kind == 'assets' and instance is None:
        obj.storage_key = f'pending/{obj.pk}'
    if hasattr(obj, 'organization_id'):
        obj.organization_id = artist.organization_id
    if spec.artist_field:
        setattr(obj, spec.artist_field, artist)
    initial = {'version': stamp(instance)} if instance else {}
    if instance and kind in {'recording-links','release-links'}:
        initial.update(service=instance.platform_object.service_id, url=instance.platform_object.url)
    if instance and kind == 'pages':
        initial['editorial_text'] = instance.content_json.get('editorial', {}).get('text', '')
    form = ScopedForm(data=data, files=files, instance=obj, initial=initial)
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField):
            field.queryset = choices(user, artist, field.queryset.model,write=not read_only)
    if kind == 'contacts' and not user.is_staff:
        form.fields['visibility'].choices = [('members','Artist members'),('public','Eligible for future publication')]
    return form
