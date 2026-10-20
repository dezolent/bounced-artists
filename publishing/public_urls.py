from django.urls import path
from . import public_api

urlpatterns = [
    path('artists/<slug:slug>/',public_api.artist_detail,name='public-artist'),
    path('artists/<slug:slug>/recordings/',public_api.collection,{'kind':'recordings'},name='public-recordings'),
    path('artists/<slug:slug>/recordings/<uuid:resource_id>/',public_api.collection,{'kind':'recordings'},name='public-recording'),
    path('artists/<slug:slug>/releases/',public_api.collection,{'kind':'releases'},name='public-releases'),
    path('artists/<slug:slug>/releases/<uuid:resource_id>/',public_api.collection,{'kind':'releases'},name='public-release'),
    path('artists/<slug:slug>/fanlinks/',public_api.collection,{'kind':'fanlinks'},name='public-fanlinks'),
    path('artists/<slug:slug>/fanlinks/<slug:resource_id>/',public_api.collection,{'kind':'fanlinks'},name='public-fanlink'),
    path('artists/<slug:slug>/artwork/<uuid:resource_id>/',public_api.artwork,name='public-artwork'),
]
