from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from core.schema import UUIDTimestamped
from .validation import reject_secrets


class Connection(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT, null=True, blank=True)
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT)
    auth_type = models.CharField(max_length=30, default="public")
    secret_reference = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, default="inactive")
    expires_at = models.DateTimeField(null=True, blank=True)
    def clean(self):
        if self.artist_id and self.artist.organization_id != self.organization_id:
            raise ValidationError({"artist": "Artist must belong to this organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class SyncJob(UUIDTimestamped):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    job_type = models.CharField(max_length=40)
    schedule = models.CharField(max_length=100, blank=True)
    cursor_json = models.JSONField(default=dict, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=False)


class SyncRun(UUIDTimestamped):
    sync_job = models.ForeignKey(SyncJob, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default="pending")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    counts_json = models.JSONField(default=dict, blank=True)
    error_summary = models.TextField(blank=True)


class SourceRecord(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    sync_run = models.ForeignKey(SyncRun, on_delete=models.SET_NULL, null=True, blank=True)
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT, null=True, blank=True)
    collector = models.CharField(max_length=100)
    collection_method = models.CharField(max_length=30, choices=[("api", "API"), ("scraper", "Scraper"), ("manual", "Manual"), ("report_import", "Report import")])
    parser_version = models.CharField(max_length=80, blank=True)
    object_type = models.CharField(max_length=80)
    external_id = models.CharField(max_length=255, blank=True)
    payload_hash = models.CharField(max_length=64)
    payload_json = models.JSONField(null=True, blank=True)
    private_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT, null=True, blank=True)
    observed_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(payload_json__isnull=False) | Q(private_asset__isnull=False), name="source_has_payload")]
        indexes = [models.Index(fields=["organization", "object_type", "observed_at"])]
    def clean(self):
        if self.sync_run_id and self.sync_run.sync_job.connection.organization_id != self.organization_id:
            raise ValidationError({"sync_run": "Run must belong to this organization."})
        if self.private_asset_id and (self.private_asset.visibility != "private" or self.private_asset.organization_id != self.organization_id):
            raise ValidationError({"private_asset": "Source asset must be private and owned by this organization."})
        reject_secrets(self.payload_json)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EntityTarget(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    artist = models.ForeignKey("artists.Artist", on_delete=models.CASCADE, null=True, blank=True)
    recording = models.ForeignKey("catalog.Recording", on_delete=models.CASCADE, null=True, blank=True)
    release = models.ForeignKey("catalog.Release", on_delete=models.CASCADE, null=True, blank=True)
    work = models.ForeignKey("rights.MusicalWork", on_delete=models.CASCADE, null=True, blank=True)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, null=True, blank=True)
    website = models.ForeignKey("publishing.Website", on_delete=models.CASCADE, null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=(Q(artist__isnull=False, recording__isnull=True, release__isnull=True, work__isnull=True, event__isnull=True, website__isnull=True) | Q(artist__isnull=True, recording__isnull=False, release__isnull=True, work__isnull=True, event__isnull=True, website__isnull=True) | Q(artist__isnull=True, recording__isnull=True, release__isnull=False, work__isnull=True, event__isnull=True, website__isnull=True) | Q(artist__isnull=True, recording__isnull=True, release__isnull=True, work__isnull=False, event__isnull=True, website__isnull=True) | Q(artist__isnull=True, recording__isnull=True, release__isnull=True, work__isnull=True, event__isnull=False, website__isnull=True) | Q(artist__isnull=True, recording__isnull=True, release__isnull=True, work__isnull=True, event__isnull=True, website__isnull=False)), name="entity_target_exactly_one")]
    def clean(self):
        for name in ("artist", "recording", "release", "work", "event", "website"):
            value = getattr(self, name, None)
            if value is not None and value.organization_id != self.organization_id:
                raise ValidationError({name: "Target must belong to this organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


TARGET_FIELDS = {
    "artist": {"name", "bio_short", "bio_long", "location", "timezone", "public_metadata"},
    "recording": {"title", "version_title", "duration_ms", "original_release_date", "explicit"},
    "release": {"title", "release_type", "release_date"},
    "work": {"title", "iswc", "status"},
    "event": {"title", "local_date", "starts_at", "ends_at", "status"},
    "website": {"locale", "theme_config"},
}


def validate_target_field(target, field_name):
    for kind, allowed in TARGET_FIELDS.items():
        if getattr(target, f"{kind}_id"):
            if field_name not in allowed:
                raise ValidationError({"field_name": "Field is not allowed for this target."})
            return
    raise ValidationError({"target": "A typed target is required."})


class FieldObservation(UUIDTimestamped):
    source_record = models.ForeignKey(SourceRecord, on_delete=models.PROTECT)
    target = models.ForeignKey(EntityTarget, on_delete=models.CASCADE)
    field_name = models.CharField(max_length=100)
    value_json = models.JSONField()
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    def clean(self):
        if self.source_record.organization_id != self.target.organization_id:
            raise ValidationError({"target": "Observation target must share source organization."})
        validate_target_field(self.target, self.field_name)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EditorialOverride(UUIDTimestamped):
    target = models.ForeignKey(EntityTarget, on_delete=models.CASCADE)
    field_name = models.CharField(max_length=100)
    value_json = models.JSONField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    def clean(self):
        validate_target_field(self.target, self.field_name)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewItem(UUIDTimestamped):
    source_record = models.ForeignKey(SourceRecord, on_delete=models.PROTECT)
    issue_type = models.CharField(max_length=50)
    status = models.CharField(max_length=20, default="open")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)


class AuditEvent(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    target = models.ForeignKey(EntityTarget, on_delete=models.SET_NULL, null=True, blank=True)
    target_label = models.CharField(max_length=300, blank=True)
    change_json = models.JSONField(default=dict)
    def clean(self):
        reject_secrets(self.change_json)
        if self.target_id and self.target.organization_id != self.organization_id:
            raise ValidationError({"target": "Audit target must share organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewCandidate(UUIDTimestamped):
    review_item = models.ForeignKey(ReviewItem, on_delete=models.CASCADE, related_name="candidates")
    target = models.ForeignKey(EntityTarget, on_delete=models.CASCADE)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["review_item", "target"], name="review_candidate_unique")]
    def clean(self):
        if self.review_item_id and self.target_id and self.review_item.source_record.organization_id != self.target.organization_id:
            raise ValidationError({"target": "Review candidate must share source organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
