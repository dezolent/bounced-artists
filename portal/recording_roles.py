"""Artist credit roles within the current authorized, active catalog."""
from django.db.models.functions import Lower, Trim

from .querysets import scoped


def _artist_credits(user, artist):
    return scoped(user, artist, 'recording-credits').filter(
        artist=artist, artist__organization=artist.organization,
        artist__archived_at__isnull=True,
    ).annotate(normalized_role=Lower(Trim('role')))


def primary_recording_ids(user, artist):
    return _artist_credits(user, artist).filter(normalized_role='primary').values('recording_id')


def appearance_recording_ids(user, artist):
    return _artist_credits(user, artist).filter(
        normalized_role__in=('featured', 'guest', 'collaborator'),
    ).exclude(recording_id__in=primary_recording_ids(user, artist)).values('recording_id')
