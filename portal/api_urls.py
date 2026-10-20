from django.urls import path
from .api import ArtistResourceAPI

urlpatterns=[
    path('artists/<uuid:artist_id>/<slug:kind>/',ArtistResourceAPI.as_view()),
    path('artists/<uuid:artist_id>/<slug:kind>/<str:object_id>/',ArtistResourceAPI.as_view()),
]
