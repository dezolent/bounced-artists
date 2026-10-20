from django.contrib import admin
from django.urls import path
from django.urls import include
from rest_framework.routers import DefaultRouter
from artists.api import ArtistViewSet
from core.views import health, ready

router = DefaultRouter()
router.register("artists", ArtistViewSet, basename="artist")
urlpatterns = [
    path('', include('portal.urls')),
    path('api/public/v1/', include('publishing.public_urls')),
    path('api/portal/v1/', include('portal.api_urls')),
    path("health/", health),
    path("ready/", ready),
    path("admin/", admin.site.urls),
    path("api/v1/", include(router.urls)),
    path("api-auth/", include("rest_framework.urls")),
]
admin.site.site_header = "Bounced Artists Administration"
admin.site.site_title = "Bounced Artists"
admin.site.index_title = "Bounced Studio / Dezolent, LLC"
