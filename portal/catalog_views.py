"""Catalog item editor route; regular entity CRUD remains available separately."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .access import artist_for, require_editor
from .catalog_editor import CatalogEditor
from .querysets import scoped
from .services import StaleEdit


def edit_catalog(request,artist_id,kind,object_id=None):
    from .views import context
    artist = artist_for(request.user,artist_id)
    require_editor(request.user,artist)
    instance = get_object_or_404(scoped(request.user,artist,kind,write=True),pk=object_id) if object_id else None
    editor = CatalogEditor(request.user,artist,kind,instance=instance,
                           data=request.POST if request.method == 'POST' else None,
                           files=request.FILES if request.method == 'POST' else None)
    status = 200
    if request.method == 'POST':
        try:
            obj = editor.save()
            if obj:
                messages.success(request,'Catalog saved. Public content changes only after review.')
                return redirect('portal:recording' if kind == 'recordings' else 'portal:release',artist_id=artist.pk,object_id=obj.pk)
            status = 400
        except (ValidationError,OSError) as error:
            editor.form.add_error(None,error.messages if isinstance(error,ValidationError) else 'File storage is unavailable. Please select your uploads again and retry.')
            status = 409 if isinstance(error,StaleEdit) else 400
    back_url = reverse('portal:recording' if kind == 'recordings' else 'portal:release',args=[artist.pk,instance.pk]) if instance else reverse('portal:catalog',args=[artist.pk])+('?kind=releases' if kind == 'releases' else '')
    return render(request,'portal/catalog_form.html',context(request,artist,editor=editor,form=editor.form,
                  title=f'{"Edit" if instance else "Add"} {"song" if kind == "recordings" else "release"}',
                  back_url=back_url,delete_url=reverse('portal:delete',args=[artist.pk,kind,instance.pk]) if instance else ''),status=status)
