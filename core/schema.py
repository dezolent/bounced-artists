import uuid
from django.db import models


class UUIDTimestamped(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class EditorialRecord(UUIDTimestamped):
    """Mutable catalog content with stable identity and reversible removal."""
    revision = models.PositiveBigIntegerField(default=1)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
