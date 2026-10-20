from rest_framework import permissions, serializers, viewsets
from .models import Artist


class ActiveAuthenticated(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_active


class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        # Explicit allowlist: contacts, membership lists, and future fields stay private.
        fields = ["id", "name", "slug", "management_status", "bio_short", "bio_long", "location", "timezone", "created_at", "updated_at"]
        read_only_fields = fields


class ArtistViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ArtistSerializer
    permission_classes = [ActiveAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        artists = Artist.objects.all()
        if self.request.user.is_staff:
            return artists
        return artists.filter(memberships__user=self.request.user)
