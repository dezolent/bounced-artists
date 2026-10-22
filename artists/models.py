import uuid
from django.conf import settings
from django.core.validators import URLValidator
from django.db import models
from core.schema import UUIDTimestamped
from django.db.models.functions import Lower
from .validators import validate_timezone


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(UUIDTimestamped):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class OrganizationMembership(UUIDTimestamped):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=10, choices=[("owner", "Owner"), ("editor", "Editor"), ("viewer", "Viewer")])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="org_member_unique")]


class Artist(Timestamped):
    class ManagementStatus(models.TextChoices):
        MANAGED = "managed", "Managed artist"
        UNMANAGED = "unmanaged", "Unmanaged collaborator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.PositiveBigIntegerField(default=1)
    archived_at = models.DateTimeField(null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="artists")
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    public_metadata = models.JSONField(default=dict, blank=True)
    bootstrap_key = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    management_status = models.CharField(max_length=16, choices=ManagementStatus, default=ManagementStatus.UNMANAGED, db_index=True)
    bio_short = models.TextField(blank=True)
    bio_long = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    timezone = models.CharField(max_length=64, blank=True, validators=[validate_timezone])
    requires_staff_review = models.BooleanField(default=False, db_default=False, help_text='Require staff to publish this artist’s website updates.')

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("slug"), name="artist_slug_ci_unique"),
            models.CheckConstraint(condition=models.Q(name__regex=r"\S"), name="artist_name_nonblank"),
            models.CheckConstraint(condition=~models.Q(slug=""), name="artist_slug_nonempty"),
            models.CheckConstraint(condition=models.Q(management_status__in=["managed", "unmanaged"]), name="artist_management_status_valid"),
        ]

    def __str__(self):
        return self.name


class ArtistMembership(Timestamped):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="artist_memberships")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role, default=Role.VIEWER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "artist"], name="membership_user_artist_unique"),
            models.CheckConstraint(condition=models.Q(role__in=["owner", "editor", "viewer"]), name="membership_role_valid"),
        ]
        indexes = [models.Index(fields=["artist", "role"])]

    def __str__(self):
        return f"{self.user} / {self.artist} ({self.role})"


class ArtistAlias(Timestamped):
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="aliases")
    name = models.CharField(max_length=200)
    alias_type = models.CharField(max_length=30, default="alternate")

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("name"), "artist", name="artist_alias_ci_unique"),
            models.CheckConstraint(condition=models.Q(name__regex=r"\S"), name="artist_alias_nonblank"),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name


class ArtistContact(Timestamped):
    class Purpose(models.TextChoices):
        GENERAL = "general", "General"
        BOOKING = "booking", "Booking"
        MANAGEMENT = "management", "Management"
        PRESS = "press", "Press"
        RIGHTS = "rights", "Rights"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Staff only"
        MEMBERS = "members", "Artist members"
        PUBLIC = "public", "Eligible for future publication"

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="contacts")
    purpose = models.CharField(max_length=16, choices=Purpose, default=Purpose.GENERAL)
    label = models.CharField(max_length=100, blank=True)
    value = models.CharField(max_length=320)
    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    visibility = models.CharField(max_length=10, choices=Visibility, default=Visibility.PRIVATE)

    class Meta:
        indexes = [models.Index(fields=["artist", "visibility", "purpose"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(visibility__in=["private", "members", "public"]), name="contact_visibility_valid"),
            models.CheckConstraint(condition=models.Q(purpose__in=["general", "booking", "management", "press", "rights"]), name="contact_purpose_valid"),
            models.CheckConstraint(condition=models.Q(value__regex=r"\S"), name="contact_value_nonblank"),
        ]

    def __str__(self):
        # Contact values are deliberately absent from incidental admin labels.
        return f"{self.artist} / {self.get_purpose_display()}"


class Service(Timestamped):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    service_type = models.CharField(max_length=30, default="platform")
    capabilities = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("slug"), name="service_slug_ci_unique"),
            models.CheckConstraint(condition=models.Q(name__regex=r"\S"), name="service_name_nonblank"),
        ]

    def __str__(self):
        return self.name


class ArtistProfileLink(Timestamped):
    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="profile_links")
    platform = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="artist_links")
    external_id = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=2048, validators=[URLValidator(schemes=["http", "https"])])
    verification_status = models.CharField(max_length=12, choices=VerificationStatus, default=VerificationStatus.UNVERIFIED)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["artist", "platform", "url"], name="artist_profile_url_unique"),
            models.UniqueConstraint(fields=["artist", "platform", "external_id"], condition=~models.Q(external_id=""), name="artist_profile_external_id_unique"),
            models.CheckConstraint(condition=models.Q(verification_status__in=["unverified", "verified", "rejected"]), name="profile_verification_valid"),
        ]

    def __str__(self):
        return f"{self.artist} / {self.platform}"


class ArtistResourceGrant(UUIDTimestamped):
    """Explicit catalog assignment. Credits and organization membership confer no access."""
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="resource_grants")
    capability = models.CharField(max_length=8, choices=[("read", "Read"), ("manage", "Manage")], default="read")
    recording = models.ForeignKey("catalog.Recording", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    release = models.ForeignKey("catalog.Release", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    asset = models.ForeignKey("catalog.Asset", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    work = models.ForeignKey("rights.MusicalWork", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    party = models.ForeignKey("rights.Party", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")

    label = models.ForeignKey("catalog.Label", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")
    release_group = models.ForeignKey("catalog.ReleaseGroup", on_delete=models.CASCADE, null=True, blank=True, related_name="artist_grants")

    KINDS = ("recording", "release", "asset", "work", "party", "event", "label", "release_group")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(capability__in=["read", "manage"]), name="artist_grant_capability_valid"),
            models.CheckConstraint(condition=(
                models.Q(recording__isnull=False, release__isnull=True, asset__isnull=True, work__isnull=True, party__isnull=True, event__isnull=True, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=False, asset__isnull=True, work__isnull=True, party__isnull=True, event__isnull=True, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=False, work__isnull=True, party__isnull=True, event__isnull=True, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=True, work__isnull=False, party__isnull=True, event__isnull=True, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=True, work__isnull=True, party__isnull=False, event__isnull=True, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=True, work__isnull=True, party__isnull=True, event__isnull=False, label__isnull=True, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=True, work__isnull=True, party__isnull=True, event__isnull=True, label__isnull=False, release_group__isnull=True) |
                models.Q(recording__isnull=True, release__isnull=True, asset__isnull=True, work__isnull=True, party__isnull=True, event__isnull=True, label__isnull=True, release_group__isnull=False)
            ), name="artist_grant_exactly_one_target"),
        ] + [models.UniqueConstraint(fields=["artist", kind], condition=models.Q(**{f"{kind}__isnull": False}), name=f"artist_grant_{kind}_unique") for kind in ("recording", "release", "asset", "work", "party", "event", "label", "release_group")]

    def clean(self):
        from django.core.exceptions import ValidationError
        targets = [getattr(self, kind) for kind in self.KINDS if getattr(self, f"{kind}_id")]
        if len(targets) != 1:
            raise ValidationError("Choose exactly one catalog record.")
        if targets[0].organization_id != self.artist.organization_id:
            raise ValidationError("Assigned record must belong to the artist's organization.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
