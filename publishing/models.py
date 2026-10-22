from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from core.schema import UUIDTimestamped, EditorialRecord
from catalog.models import validate_asset_ownership


class Website(EditorialRecord):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT)
    domain = models.CharField(max_length=255, unique=True)
    repository = models.CharField(max_length=255, blank=True)
    hosting_provider = models.CharField(max_length=60, blank=True)
    locale = models.CharField(max_length=20, default="en")
    theme_config = models.JSONField(default=dict, blank=True)
    def __str__(self): return self.domain
    def clean(self):
        if self.artist_id and self.artist.organization_id != self.organization_id:
            raise ValidationError({"artist": "Website artist must belong to this organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Page(EditorialRecord):
    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="pages")
    path = models.CharField(max_length=500)
    page_type = models.CharField(max_length=40)
    title = models.CharField(max_length=300)
    content_json = models.JSONField(default=dict)
    publication_status = models.CharField(max_length=20, default="draft", choices=[("draft", "Draft"), ("approved", "Approved"), ("published", "Published")])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["website", "path"], name="website_page_path_unique")]


class SEORecord(EditorialRecord):
    page = models.OneToOneField(Page, on_delete=models.CASCADE)
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    canonical_url = models.URLField(max_length=2048, blank=True)
    og_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT, null=True, blank=True)
    robots = models.CharField(max_length=100, blank=True)
    structured_data_overrides = models.JSONField(default=dict, blank=True)
    def clean(self):
        if self.og_asset_id:
            validate_asset_ownership(self.og_asset, self.page.website.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FanlinkPage(EditorialRecord):
    website = models.ForeignKey(Website, on_delete=models.CASCADE)
    recording = models.ForeignKey("catalog.Recording", on_delete=models.PROTECT, null=True, blank=True)
    release = models.ForeignKey("catalog.Release", on_delete=models.PROTECT, null=True, blank=True)
    slug = models.SlugField(max_length=200)
    title = models.CharField(max_length=300)
    status = models.CharField(max_length=20, choices=[("draft", "Draft"), ("approved", "Approved"), ("published", "Published")], default="draft")
    class Meta:
        constraints = [models.CheckConstraint(condition=(Q(recording__isnull=False, release__isnull=True) | Q(recording__isnull=True, release__isnull=False)), name="fanlink_exactly_one_target"), models.UniqueConstraint(fields=["website", "slug"], name="fanlink_website_slug_unique")]
    def clean(self):
        target = self.recording if self.recording_id else self.release if self.release_id else None
        if target and target.organization_id != self.website.organization_id:
            raise ValidationError("Fanlink target must belong to website organization.")
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FanlinkDestination(UUIDTimestamped):
    fanlink_page = models.ForeignKey(FanlinkPage, on_delete=models.CASCADE, related_name="destinations")
    platform_object = models.ForeignKey("catalog.PlatformObject", on_delete=models.PROTECT)
    label = models.CharField(max_length=100, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["fanlink_page", "platform_object"], name="fanlink_destination_unique")]


class PublicationOutbox(UUIDTimestamped):
    website = models.ForeignKey(Website, on_delete=models.CASCADE)
    content_version = models.PositiveBigIntegerField()
    idempotency_key = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=20, choices=[("pending", "Pending"), ("processing", "Processing"), ("delivered", "Delivered"), ("failed", "Failed")], default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["website", "content_version"], name="outbox_version_unique"), models.UniqueConstraint(fields=["website", "idempotency_key"], condition=~Q(idempotency_key=""), name="outbox_idempotency_unique")]


class WebsitePublication(UUIDTimestamped):
    website = models.ForeignKey(Website, on_delete=models.CASCADE)
    content_version = models.PositiveBigIntegerField()
    snapshot_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT)
    deployment_reference = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=[("prepared", "Prepared"), ("published", "Published"), ("failed", "Failed")], default="prepared")
    published_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["website", "content_version"], name="publication_version_unique")]
    def clean(self):
        if self.website_id and self.snapshot_asset_id:
            validate_asset_ownership(self.snapshot_asset, self.website.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ApprovedRevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Approved catalog revisions are immutable.')

    def delete(self):
        raise ValidationError('Approved catalog revisions are retained; revoke instead.')

    def bulk_create(self, objs, **kwargs):
        for obj in objs: obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class ApprovedCatalogRevision(models.Model):
    """Immutable staff-reviewed projection; never a live ORM serialization."""
    import uuid as _uuid
    from django.conf import settings as _settings
    id = models.UUIDField(primary_key=True, default=_uuid.uuid4, editable=False)
    website = models.ForeignKey(Website, on_delete=models.PROTECT, related_name='approved_revisions')
    objects = ApprovedRevisionQuerySet.as_manager()
    schema_version = models.PositiveSmallIntegerField(default=1, editable=False)
    content_version = models.PositiveBigIntegerField()
    approver = models.ForeignKey(_settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(auto_now_add=True)
    checksum = models.CharField(max_length=64)
    source_checksum = models.CharField(max_length=64)
    payload = models.JSONField()
    selection = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['website','content_version'], name='approved_catalog_version_unique')]

    def clean(self):
        from .projection import validate_payload, digest, normalize_selection
        validate_payload(self.payload, schema_version=self.schema_version)
        if normalize_selection(self.selection, schema_version=self.schema_version) != self.selection:
            raise ValidationError('Invalid approved resource selection.')
        if self.checksum != digest(self.payload):
            raise ValidationError('Invalid approved projection checksum or schema.')
        from .projection import require_publisher
        require_publisher(self.approver, self.website)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Approved catalog revisions are immutable.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Approved catalog revisions are retained; revoke the active pointer instead.')


class WebsiteApprovedState(models.Model):
    website = models.OneToOneField(Website, primary_key=True, on_delete=models.PROTECT, related_name='approved_state')
    active_revision = models.OneToOneField(ApprovedCatalogRevision, null=True, blank=True, on_delete=models.PROTECT)
    approved_slug = models.SlugField(max_length=200, unique=True)

    def clean(self):
        if self.active_revision_id and self.active_revision.website_id != self.website_id:
            raise ValidationError('Active revision must belong to this website.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ApprovedArtwork(models.Model):
    revision = models.ForeignKey(ApprovedCatalogRevision, on_delete=models.PROTECT, related_name='artwork_versions')
    version = models.ForeignKey('catalog.AssetVersion', on_delete=models.PROTECT)
    public_id = models.UUIDField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['revision','public_id'], name='approved_artwork_unique')]

    def clean(self):
        from .projection import public_id, artwork_urls
        asset=self.version.asset
        if str(asset.pk) not in self.revision.selection.get('artwork',[]) or asset.organization_id != self.revision.website.organization_id:
            raise ValidationError('Artwork must be explicitly selected for this revision.')
        expected=public_id('artwork',self.version_id)
        url=f'/api/public/v1/artists/{self.revision.payload["artist"]["slug"]}/artwork/{expected}/'
        if str(self.public_id) != expected or url not in artwork_urls(self.revision.payload):
            raise ValidationError('Artwork reference must match the approved payload.')

    def save(self,*args,**kwargs):
        if not self._state.adding: raise ValidationError('Approved artwork references are immutable.')
        self.full_clean()
        return super().save(*args,**kwargs)
