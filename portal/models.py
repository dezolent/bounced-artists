from django.db import models


class LoginThrottle(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    window_started = models.DateTimeField()


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
    requested_by = models.ForeignKey('core.User',on_delete=models.SET_NULL,null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10,choices=[('pending','Pending'),('resolved','Resolved')],default='pending')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['artist'],condition=models.Q(status='pending'),name='one_pending_artist_review')]
