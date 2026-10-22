from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import Q
from core.schema import UUIDTimestamped, EditorialRecord


def validate_asset_ownership(asset, organization_id):
    if asset.organization_id is not None and asset.organization_id != organization_id:
        raise ValidationError({"asset": "Asset must belong to this organization."})
    if asset.visibility != "public" and asset.organization_id is None:
        raise ValidationError({"asset": "Private asset needs organization ownership."})


DATE_PRECISION = [("unknown", "Unknown"), ("year", "Year"), ("month", "Month"), ("day", "Day")]
STATUS = [("draft", "Draft"), ("review", "Review"), ("approved", "Approved"), ("archived", "Archived")]
REVIEW_STATUS = [("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")]
AVAILABILITY_STATUS = [("unknown", "Unknown"), ("available", "Available"), ("unavailable", "Unavailable"), ("removed", "Removed")]


def validate_secondary_types(value):
    if not isinstance(value, list) or len(value) > 10 or any(not isinstance(item, str) or not item.strip() or len(item) > 30 for item in value):
        raise ValidationError("Choose at most 10 secondary types, each up to 30 characters.")


class ValidatedCatalogLink(UUIDTimestamped):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Label(EditorialRecord):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    name = models.CharField(max_length=300)
    disambiguation = models.CharField(max_length=255, blank=True)
    mbid = models.UUIDField(null=True, blank=True)
    party = models.ForeignKey("rights.Party", on_delete=models.PROTECT, null=True, blank=True, related_name="catalog_labels")

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.party_id and self.party.organization_id not in (None, self.organization_id):
            raise ValidationError({"party": "Private party must share label organization."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReleaseGroup(EditorialRecord):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    primary_type = models.CharField(max_length=30, default="album")
    secondary_types = models.JSONField(default=list, blank=True, validators=[validate_secondary_types])
    mbid = models.UUIDField(null=True, blank=True)
    first_release_date = models.DateField(null=True, blank=True)
    date_precision = models.CharField(max_length=10, choices=DATE_PRECISION, default="unknown")
    disambiguation = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="draft")

    def __str__(self):
        return self.title

    def clean(self):
        # JSONField treats {} as an empty value and skips field validators.
        try:
            validate_secondary_types(self.secondary_types)
        except ValidationError as exc:
            raise ValidationError({"secondary_types": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Recording(EditorialRecord):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    version_title = models.CharField(max_length=200, blank=True)
    recording_type = models.CharField(max_length=30, default="original")
    language = models.CharField(max_length=30, blank=True)
    disambiguation = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True, max_length=5000, validators=[MaxLengthValidator(5000)])
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    explicit = models.BooleanField(null=True, blank=True)
    original_release_date = models.DateField(null=True, blank=True)
    date_precision = models.CharField(max_length=10, choices=DATE_PRECISION, default="unknown")
    status = models.CharField(max_length=12, choices=STATUS, default="draft")

    def __str__(self): return self.title


class RecordingArtist(UUIDTimestamped):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="credits")
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT)
    role = models.CharField(max_length=30, default="primary")
    billing_order = models.PositiveSmallIntegerField(default=1)
    credited_as = models.CharField(max_length=300, blank=True)
    join_phrase = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "artist", "role"], name="recording_credit_unique")]


class RecordingIdentifier(UUIDTimestamped):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="identifiers")
    scheme = models.CharField(max_length=30)
    value = models.CharField(max_length=255)
    verification_status = models.CharField(max_length=20, choices=[("unverified", "Unverified"), ("verified", "Verified"), ("rejected", "Rejected")], default="unverified")
    source_record = models.ForeignKey("provenance.SourceRecord", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "scheme", "value"], name="recording_identifier_unique")]


class RecordingRelation(UUIDTimestamped):
    from_recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="outgoing_relations")
    to_recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="incoming_relations")
    relation_type = models.CharField(max_length=30)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["from_recording", "to_recording", "relation_type"], name="recording_relation_unique"), models.CheckConstraint(condition=~Q(from_recording=models.F("to_recording")), name="recording_no_self_relation")]


class Release(EditorialRecord):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    release_type = models.CharField(max_length=30, default="single")
    release_group = models.ForeignKey(ReleaseGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="releases")
    edition = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=3, blank=True)
    language = models.CharField(max_length=30, blank=True)
    script = models.CharField(max_length=30, blank=True)
    packaging = models.CharField(max_length=100, blank=True)
    barcode = models.CharField(max_length=64, blank=True)
    catalog_number = models.CharField(max_length=100, blank=True)
    disambiguation = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True, max_length=5000, validators=[MaxLengthValidator(5000)])
    release_date = models.DateField(null=True, blank=True)
    date_precision = models.CharField(max_length=10, choices=DATE_PRECISION, default="unknown")
    label_party = models.ForeignKey("rights.Party", on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="draft")

    def __str__(self): return self.title
    def clean(self):
        if self.release_group_id and self.release_group.organization_id != self.organization_id:
            raise ValidationError({"release_group": "Release group must share release organization."})
        if self.label_party_id and self.label_party.organization_id not in (None, self.organization_id):
            raise ValidationError({"label_party": "Private label party must share release organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReleaseArtist(UUIDTimestamped):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="credits")
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT)
    role = models.CharField(max_length=30, default="primary")
    billing_order = models.PositiveSmallIntegerField(default=1)
    credited_as = models.CharField(max_length=300, blank=True)
    join_phrase = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "artist", "role"], name="release_credit_unique")]


class ReleaseIdentifier(UUIDTimestamped):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="identifiers")
    scheme = models.CharField(max_length=30)
    value = models.CharField(max_length=255)
    source_record = models.ForeignKey("provenance.SourceRecord", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "scheme", "value"], name="release_identifier_unique")]


class Medium(ValidatedCatalogLink):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="media")
    position = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=300, blank=True)
    format = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["release", "position"], name="medium_release_position_unique"),
            models.CheckConstraint(condition=Q(position__gte=1), name="medium_positive_position"),
        ]

    def __str__(self):
        return f"{self.release} — Disc {self.position}"

    def clean(self):
        if self.pk and self.tracks.exclude(release_id=self.release_id, disc_number=self.position).exists():
            raise ValidationError("Medium must retain its tracks' release and disc number.")


class ReleaseTrack(EditorialRecord):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="tracks")
    recording = models.ForeignKey(Recording, on_delete=models.PROTECT, related_name="release_tracks")
    disc_number = models.PositiveSmallIntegerField(default=1)
    track_number = models.PositiveSmallIntegerField()
    display_title = models.CharField(max_length=300, blank=True)
    medium = models.ForeignKey(Medium, on_delete=models.SET_NULL, null=True, blank=True, related_name="tracks")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    mbid = models.UUIDField(null=True, blank=True)
    credit_text = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "disc_number", "track_number"], condition=Q(archived_at__isnull=True), name="release_track_position_unique"), models.CheckConstraint(condition=Q(disc_number__gte=1, track_number__gte=1), name="release_track_positive_position")]
    def __str__(self):
        return f"{self.release} — Disc {self.disc_number}, Track {self.track_number} — {self.display_title or self.recording}"

    def clean(self):
        if self.medium_id and (self.medium.release_id != self.release_id or self.medium.position != self.disc_number):
            raise ValidationError({"medium": "Medium must belong to this release and disc number."})
        if self.release_id and self.recording_id and self.release.organization_id != self.recording.organization_id:
            raise ValidationError({"recording": "Recording and release must share organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Genre(UUIDTimestamped):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    def __str__(self): return self.name


class ArtistGenre(UUIDTimestamped):
    artist = models.ForeignKey("artists.Artist", on_delete=models.CASCADE)
    genre = models.ForeignKey(Genre, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["artist", "genre"], name="artist_genre_unique")]


class RecordingGenre(UUIDTimestamped):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE)
    genre = models.ForeignKey(Genre, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "genre"], name="recording_genre_unique")]


class ReleaseGenre(ValidatedCatalogLink):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="genres")
    genre = models.ForeignKey(Genre, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "genre"], name="release_genre_unique")]

    def __str__(self):
        return f"{self.release} — {self.genre}"


class ReleaseGroupGenre(ValidatedCatalogLink):
    release_group = models.ForeignKey(ReleaseGroup, on_delete=models.CASCADE, related_name="genres")
    genre = models.ForeignKey(Genre, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["release_group", "genre"], name="release_group_genre_unique")]

    def __str__(self):
        return f"{self.release_group} — {self.genre}"


class RecordingLabel(ValidatedCatalogLink):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE, related_name="labels")
    label = models.ForeignKey(Label, on_delete=models.PROTECT, related_name="recording_links")
    position = models.PositiveSmallIntegerField(default=1)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["recording", "label"], name="recording_label_unique"),
            models.UniqueConstraint(fields=["recording"], condition=Q(is_primary=True), name="recording_one_primary_label"),
            models.CheckConstraint(condition=Q(position__gte=1), name="recording_label_positive_position"),
        ]

    def __str__(self):
        return f"{self.recording} — {self.label}"

    def clean(self):
        if self.recording_id and self.label_id and self.recording.organization_id != self.label.organization_id:
            raise ValidationError({"label": "Label must share recording organization."})


class ReleaseLabel(ValidatedCatalogLink):
    release = models.ForeignKey(Release, on_delete=models.CASCADE, related_name="labels")
    label = models.ForeignKey(Label, on_delete=models.PROTECT, related_name="release_links")
    position = models.PositiveSmallIntegerField(default=1)
    is_primary = models.BooleanField(default=False)
    catalog_number = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["release", "label"], name="release_label_unique"),
            models.UniqueConstraint(fields=["release"], condition=Q(is_primary=True), name="release_one_primary_label"),
            models.CheckConstraint(condition=Q(position__gte=1), name="release_label_positive_position"),
        ]

    def __str__(self):
        return f"{self.release} — {self.label}"

    def clean(self):
        if self.release_id and self.label_id and self.release.organization_id != self.label.organization_id:
            raise ValidationError({"label": "Label must share release organization."})


class Asset(EditorialRecord):
    class Category(models.TextChoices):
        LYRICS_TEXT = 'lyrics_text', 'Lyrics · text'
        LYRICS_SRT = 'lyrics_srt', 'Lyrics · SRT'
        COVER_ART = 'cover_art', 'Cover art'
        ARTIST_LOGO = 'artist_logo', 'Artist logos'
        PROFILE_IMAGE = 'profile_image', 'Profile images'
        BANNER_IMAGE = 'banner_image', 'Banner images'
        ARTIST_ICON = 'artist_icon', 'Artist icons / favicons'
        WEBSITE_ASSET = 'website_asset', 'Website assets'
        AUDIO = 'audio', 'Audio'
        VIDEO = 'video', 'Video'
        DOCUMENT = 'document', 'Documents / other text'

    category = models.CharField(max_length=30, choices=Category.choices, blank=True, db_index=True)
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT, null=True, blank=True)
    storage_key = models.CharField(max_length=512, blank=True)
    source_url = models.URLField(max_length=2048, blank=True)
    source_repository = models.CharField(max_length=255, blank=True)
    source_path = models.CharField(max_length=512, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    rights_basis = models.CharField(max_length=100, blank=True)
    visibility = models.CharField(max_length=10, choices=[("private", "Private"), ("members", "Members"), ("public", "Public")], default="private")
    display_name = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    rights_approved = models.BooleanField(default=False)
    current_version = models.ForeignKey("AssetVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="current_for_assets")

    class Meta:
        constraints = [models.CheckConstraint(condition=~(Q(storage_key="") & Q(source_url="") & Q(source_repository="")), name="asset_has_location"), models.CheckConstraint(condition=Q(source_repository="", source_path="") | (~Q(source_repository="") & ~Q(source_path="")), name="asset_repo_path_pair"), models.CheckConstraint(condition=Q(visibility="public") | Q(organization__isnull=False), name="private_asset_has_owner")]

    def __str__(self):
        return self.display_name or "Catalog asset"

    def clean(self):
        if self.current_version_id and self.current_version.asset_id != self.pk:
            raise ValidationError({'current_version':'A file version must belong to this asset.'})

    def save(self,*args,**kwargs):
        self.full_clean()
        return super().save(*args,**kwargs)


class AssetVersion(UUIDTimestamped):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="versions")
    file = models.FileField(upload_to="catalog/%Y/%m/", max_length=512)
    preview_file = models.FileField(upload_to="previews/%Y/%m/", max_length=512, blank=True)
    preview_width = models.PositiveIntegerField(null=True, blank=True)
    preview_height = models.PositiveIntegerField(null=True, blank=True)
    original_name = models.CharField(max_length=255)
    byte_size = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    creator = models.ForeignKey("core.User", on_delete=models.SET_NULL, null=True, blank=True)

    def clean(self):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("File versions are immutable; upload a replacement instead.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ArtistAsset(UUIDTimestamped):
    artist = models.ForeignKey("artists.Artist", on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=30)
    sort_order = models.PositiveSmallIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["artist", "asset", "purpose"], name="artist_asset_unique")]
    def clean(self):
        if self.artist_id and self.asset_id:
            validate_asset_ownership(self.asset, self.artist.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReleaseAsset(UUIDTimestamped):
    release = models.ForeignKey(Release, on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=30)
    is_primary = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "asset", "purpose"], name="release_asset_unique"), models.UniqueConstraint(fields=["release", "purpose"], condition=Q(is_primary=True), name="release_primary_asset_unique")]
    def clean(self):
        if self.release_id and self.asset_id:
            validate_asset_ownership(self.asset, self.release.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RecordingAsset(UUIDTimestamped):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    purpose = models.CharField(max_length=30)
    is_primary = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "asset", "purpose"], name="recording_asset_unique"), models.UniqueConstraint(fields=["recording", "purpose"], condition=Q(is_primary=True), name="recording_primary_asset_unique")]
    def clean(self):
        if self.recording_id and self.asset_id:
            validate_asset_ownership(self.asset, self.recording.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PlatformObject(UUIDTimestamped):
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT)
    object_type = models.CharField(max_length=30)
    external_id = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=2048, blank=True)
    title = models.CharField(max_length=300, blank=True)
    availability_status = models.CharField(max_length=30, choices=AVAILABILITY_STATUS, default="unknown")
    uploaded_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(external_id="") | ~Q(url=""), name="platform_object_has_locator"), models.UniqueConstraint(fields=["service", "object_type", "external_id"], condition=~Q(external_id=""), name="platform_id_unique"), models.UniqueConstraint(fields=["service", "object_type", "url"], condition=Q(external_id=""), name="platform_url_only_unique")]

    def __str__(self): return f"{self.service_id}: {self.object_type} / {self.external_id or self.url}"


class RecordingPlatformMatch(UUIDTimestamped):
    recording = models.ForeignKey(Recording, on_delete=models.CASCADE)
    platform_object = models.ForeignKey(PlatformObject, on_delete=models.PROTECT)
    relationship_type = models.CharField(max_length=30, default="full_recording")
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    match_method = models.CharField(max_length=30, default="manual")
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default="pending")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "platform_object", "relationship_type"], name="recording_platform_match_unique")]


class ReleasePlatformMatch(UUIDTimestamped):
    release = models.ForeignKey(Release, on_delete=models.CASCADE)
    platform_object = models.ForeignKey(PlatformObject, on_delete=models.PROTECT)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default="pending")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["release", "platform_object"], name="release_platform_match_unique")]


class ArtistPlatformMatch(UUIDTimestamped):
    artist = models.ForeignKey("artists.Artist", on_delete=models.CASCADE)
    platform_object = models.ForeignKey(PlatformObject, on_delete=models.PROTECT)
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default="pending")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["artist", "platform_object"], name="artist_platform_match_unique")]


class PlatformAvailability(UUIDTimestamped):
    platform_object = models.ForeignKey(PlatformObject, on_delete=models.CASCADE)
    territory = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=AVAILABILITY_STATUS)
    checked_at = models.DateTimeField()
    class Meta:
        indexes = [models.Index(fields=["platform_object", "territory", "checked_at"])]
