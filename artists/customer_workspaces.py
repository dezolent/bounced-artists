"""Explicitly authorized collection customers, identified independently of names/slugs."""
from .models import Artist

WORKSPACE_WEBSITES = {
    "dezolent": "https://dezolent.com/",
    "morbeato": "https://morbeato.com/",
    "sry4u": "https://sry4u.com/",
}


def collection_artists():
    return Artist.objects.filter(organization__slug="bounced-studio",
        bootstrap_key__in=WORKSPACE_WEBSITES, management_status="managed", archived_at__isnull=True)
