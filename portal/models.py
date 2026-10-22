from django.db import models
from django.utils import timezone
from core.schema import UUIDTimestamped


class LoginThrottle(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    window_started = models.DateTimeField()


class OperationRemoval(UUIDTimestamped):
    """Reversible staff removal of a relationship without inventing artist ownership."""
    organization=models.ForeignKey('artists.Organization',on_delete=models.PROTECT,null=True,blank=True)
    model_label=models.CharField(max_length=100)
    object_id=models.CharField(max_length=40)
    removed_by=models.ForeignKey('core.User',on_delete=models.SET_NULL,null=True,blank=True,related_name='operation_removals')
    removed_at=models.DateTimeField(default=timezone.now)
    restored_by=models.ForeignKey('core.User',on_delete=models.SET_NULL,null=True,blank=True,related_name='operation_restorations')
    restored_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['model_label','object_id'],name='operation_removal_unique')]


class EditorialChange(models.Model):
    """Private change history for children that lack an EntityTarget foreign key."""
    organization = models.ForeignKey('artists.Organization', on_delete=models.PROTECT)
    artist = models.ForeignKey('artists.Artist', on_delete=models.PROTECT)
    user = models.ForeignKey('core.User', on_delete=models.SET_NULL, null=True)
    model_label = models.CharField(max_length=100)
    object_id = models.CharField(max_length=40)
    action = models.CharField(max_length=30)
    changed_fields = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class CatalogReviewRequest(models.Model):
    artist = models.ForeignKey('artists.Artist',on_delete=models.PROTECT)
    requested_by = models.ForeignKey('core.User',on_delete=models.SET_NULL,null=True,blank=True)
    website = models.ForeignKey('publishing.Website',on_delete=models.PROTECT,null=True,blank=True)
    publication_selection = models.JSONField(default=dict,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10,choices=[('pending','Pending'),('resolved','Resolved'),('returned','Changes requested')],default='pending')

    def clean(self):
        from django.core.exceptions import ValidationError
        from provenance.validation import reject_secrets
        if self.website_id and (self.website.artist_id != self.artist_id or self.website.organization_id != self.artist.organization_id):
            raise ValidationError({'website':'Publication website must belong to the requested artist and organization.'})
        reject_secrets(self.publication_selection)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['artist'],condition=models.Q(status='pending'),name='one_pending_artist_review')]
