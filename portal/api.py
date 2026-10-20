from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.http import Http404
from rest_framework.generics import get_object_or_404
from rest_framework import permissions
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView
from .access import artist_for, require_editor
from .forms import stamp
from .querysets import scoped
from .resources import resource
from .services import mutate, remove, StaleEdit


def serialize(obj, kind):
    data={'id':str(obj.pk),'version':stamp(obj)}
    for name in resource(kind).fields:
        field=obj._meta.get_field(name)
        value=getattr(obj,f'{name}_id' if field.is_relation else name)
        data[name]=str(value) if field.is_relation and value is not None else value
    return data


class ArtistResourceAPI(APIView):
    permission_classes=[permissions.IsAuthenticated]
    parser_classes=[JSONParser,MultiPartParser,FormParser]

    def dispatch(self,request,*args,**kwargs):
        self.kind=kwargs['kind']
        return super().dispatch(request,*args,**kwargs)

    def artist(self,request,artist_id):
        return artist_for(request.user,artist_id)

    def get(self,request,artist_id,kind,object_id=None):
        artist=self.artist(request,artist_id)
        rows=scoped(request.user,artist,kind).order_by('pk')
        if object_id:
            return Response(serialize(get_object_or_404(rows,pk=object_id),kind))
        page=Paginator(rows,50).get_page(request.query_params.get('page'))
        return Response({'count':page.paginator.count,'results':[serialize(obj,kind) for obj in page],'next':f'?page={page.next_page_number()}' if page.has_next() else None})

    def save(self,request,artist_id,kind,object_id=None):
        artist=self.artist(request,artist_id)
        require_editor(request.user,artist)
        allowed=set(resource(kind).fields)|{'version'}
        if kind=='assets': allowed.add('upload')
        if kind in ('recording-links','release-links'): allowed.update(('service','url'))
        if kind=='pages': allowed.add('editorial_text')
        if set(request.data)-allowed:
            return Response({'detail':'Unexpected fields in this request.'},status=400)
        data=request.data.copy()
        # PATCH preserves unspecified fields while validating the complete form.
        if object_id is not None:
            instance=get_object_or_404(scoped(request.user,artist,kind,write=True),pk=object_id)
            from django.forms.models import model_to_dict
            for name,value in model_to_dict(instance,fields=resource(kind).fields).items():
                if name not in data:
                    data[name]=value
            if kind in ('recording-links','release-links'):
                data.setdefault('service',instance.platform_object.service_id)
                data.setdefault('url',instance.platform_object.url)
            if kind=='pages': data.setdefault('editorial_text',instance.content_json.get('editorial',{}).get('text',''))
        try:
            obj,form=mutate(request.user,artist,kind,data,request.FILES,object_id)
        except DjangoValidationError as error:
            return Response({'detail':error.messages},status=409 if isinstance(error,StaleEdit) else 400)
        except OSError:
            return Response({'detail':'File storage is unavailable.'},status=503)
        if not obj:
            return Response({'errors':form.errors.get_json_data()},status=400)
        return Response(serialize(obj,kind),status=200 if object_id else 201)

    def post(self,request,artist_id,kind,object_id=None):
        if object_id:
            return Response(status=405)
        return self.save(request,artist_id,kind)

    def patch(self,request,artist_id,kind,object_id=None):
        if object_id is None:
            return Response(status=405)
        return self.save(request,artist_id,kind,object_id)

    def delete(self,request,artist_id,kind,object_id=None):
        if object_id is None:
            return Response(status=405)
        artist=self.artist(request,artist_id)
        try:
            remove(request.user,artist,kind,object_id,request.data.get('version'))
        except DjangoValidationError as error:
            return Response({'detail':error.messages},status=409 if isinstance(error,StaleEdit) else 400)
        return Response(status=204)
