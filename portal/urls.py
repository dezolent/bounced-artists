from django.urls import path
from . import views
from .review import review

app_name='portal'
urlpatterns=[
    path('',views.ArtistLoginView.as_view(),name='login'),
    path('logout/',views.ArtistLogoutView.as_view(),name='logout'),
    path('app/',views.overview,name='overview'),
    path('app/artists/<uuid:artist_id>/',views.artist_detail,name='artist'),
    path('app/artists/<uuid:artist_id>/catalog/',views.catalog,name='catalog'),
    path('app/artists/<uuid:artist_id>/review/<uuid:website_id>/',review,name='review'),
    path('app/artists/<uuid:artist_id>/request-review/',views.request_review,name='request-review'),
    path('app/artists/<uuid:artist_id>/recordings/<uuid:object_id>/',views.recording_detail,name='recording'),
    path('app/artists/<uuid:artist_id>/releases/<uuid:object_id>/',views.release_detail,name='release'),
    path('app/artists/<uuid:artist_id>/assets/<uuid:object_id>/<str:action>/',views.asset_media,name='asset-media'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/',views.resource_list,name='resources'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/new/',views.edit_resource,name='create'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/',views.resource_detail,name='detail'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/edit/',views.edit_resource,name='edit'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/delete/',views.delete_resource,name='delete'),
]
