from django.db import models
from django.db.models import Q, F
from core.schema import UUIDTimestamped


class Party(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT, null=True, blank=True)
    party_type = models.CharField(max_length=30)
    legal_name = models.CharField(max_length=300, blank=True)
    display_name = models.CharField(max_length=300)
    def __str__(self): return self.display_name


class PartyIdentifier(UUIDTimestamped):
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="identifiers")
    scheme = models.CharField(max_length=30)
    value = models.CharField(max_length=255)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["party", "scheme", "value"], name="party_identifier_unique")]


class ArtistParty(UUIDTimestamped):
    artist = models.ForeignKey("artists.Artist", on_delete=models.CASCADE)
    party = models.ForeignKey(Party, on_delete=models.PROTECT)
    relationship_type = models.CharField(max_length=30)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["artist", "party", "relationship_type"], name="artist_party_unique")]


class RightsSociety(UUIDTimestamped):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=30, unique=True)
    society_type = models.CharField(max_length=30)
    territory = models.CharField(max_length=3, blank=True)
    def __str__(self): return self.code


class PartyAffiliation(UUIDTimestamped):
    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    society = models.ForeignKey(RightsSociety, on_delete=models.PROTECT)
    membership_reference = models.CharField(max_length=255, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gte=F("valid_from")), name="party_affiliation_dates_valid")]


class MusicalWork(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    iswc = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=30, default="unverified", choices=[("unverified", "Unverified"), ("registered", "Registered"), ("confirmed_unregistered", "Confirmed unregistered"), ("not_found", "Not found")])
    def __str__(self): return self.title


class WorkAlias(UUIDTimestamped):
    work = models.ForeignKey(MusicalWork, on_delete=models.CASCADE)
    title = models.CharField(max_length=300)
    alias_type = models.CharField(max_length=30, default="alternate")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "title", "alias_type"], name="work_alias_unique")]


class WorkContributor(UUIDTimestamped):
    work = models.ForeignKey(MusicalWork, on_delete=models.CASCADE)
    party = models.ForeignKey(Party, on_delete=models.PROTECT)
    role = models.CharField(max_length=30)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "party", "role"], name="work_contributor_unique")]


class RecordingWork(UUIDTimestamped):
    recording = models.ForeignKey("catalog.Recording", on_delete=models.CASCADE)
    work = models.ForeignKey(MusicalWork, on_delete=models.PROTECT)
    relationship_type = models.CharField(max_length=30, default="embodies")
    verification_status = models.CharField(max_length=20, default="unverified")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["recording", "work", "relationship_type"], name="recording_work_unique")]


class WorkRegistration(UUIDTimestamped):
    work = models.ForeignKey(MusicalWork, on_delete=models.CASCADE)
    society = models.ForeignKey(RightsSociety, on_delete=models.PROTECT)
    external_work_id = models.CharField(max_length=255, blank=True)
    registration_status = models.CharField(max_length=30, default="unverified", choices=[("unverified", "Unverified"), ("pending", "Pending"), ("registered", "Registered"), ("not_found", "Not found"), ("confirmed_unregistered", "Confirmed unregistered")])
    last_verified_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "society", "external_work_id"], condition=~Q(external_work_id=""), name="work_registration_id_unique")]


class RightsClaim(UUIDTimestamped):
    registration = models.ForeignKey(WorkRegistration, on_delete=models.CASCADE, related_name="claims")
    party = models.ForeignKey(Party, on_delete=models.PROTECT)
    right_type = models.CharField(max_length=30)
    share_value = models.DecimalField(max_digits=11, decimal_places=6, null=True, blank=True)
    share_basis = models.CharField(max_length=50, blank=True)
    territory = models.CharField(max_length=3, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    source_record = models.ForeignKey("provenance.SourceRecord", on_delete=models.PROTECT, null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(share_value__isnull=True) | Q(share_value__gte=0), name="rights_share_nonnegative"), models.CheckConstraint(condition=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gte=F("valid_from")), name="rights_claim_dates_valid")]
    def clean(self):
        if self.source_record_id and self.source_record.organization_id != self.registration.work.organization_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({"source_record": "Claim source must share work organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
