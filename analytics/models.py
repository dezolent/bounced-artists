import hashlib
import json
from django.db import models
from django.db.models import Q
from core.schema import UUIDTimestamped
from django.core.exceptions import ValidationError
from catalog.models import validate_asset_ownership


def normalize_dimensions(value):
    if not isinstance(value, dict):
        raise ValueError("Metric dimensions must be an object")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dimension_hash(value):
    return hashlib.sha256(normalize_dimensions(value).encode("utf-8")).hexdigest()


class MetricDefinition(UUIDTimestamped):
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT)
    code = models.CharField(max_length=100)
    unit = models.CharField(max_length=30)
    aggregation_rule = models.CharField(max_length=30, choices=[("cumulative", "Cumulative"), ("period_total", "Period total"), ("rolling", "Rolling window"), ("gauge", "Gauge"), ("unique", "Unique count")])
    period_type = models.CharField(max_length=30)
    definition_version = models.PositiveSmallIntegerField(default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["service", "code", "definition_version"], name="metric_definition_version_unique")]


class MetricSeries(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    metric_definition = models.ForeignKey(MetricDefinition, on_delete=models.PROTECT)
    platform_object = models.ForeignKey("catalog.PlatformObject", on_delete=models.PROTECT)
    dimensions_json = models.JSONField(default=dict)
    dimensions_hash = models.CharField(max_length=64, editable=False)
    source_method = models.CharField(max_length=30)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "metric_definition", "platform_object", "dimensions_hash", "source_method"], name="metric_series_dimensions_unique")]
    def clean(self):
        if self.metric_definition_id and self.platform_object_id and self.metric_definition.service_id != self.platform_object.service_id:
            raise ValidationError({"platform_object": "Metric service must match platform service."})
    def save(self, *args, **kwargs):
        self.dimensions_hash = dimension_hash(self.dimensions_json)
        self.full_clean()
        return super().save(*args, **kwargs)


class MetricObservation(UUIDTimestamped):
    series = models.ForeignKey(MetricSeries, on_delete=models.CASCADE, related_name="observations")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    observed_at = models.DateTimeField()
    value = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    source_record = models.ForeignKey("provenance.SourceRecord", on_delete=models.PROTECT, null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    quality_status = models.CharField(max_length=20, default="pending", choices=[("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["series", "period_start", "period_end", "revision"], name="metric_observation_revision_unique"), models.CheckConstraint(condition=Q(period_end__gte=models.F("period_start")), name="metric_period_valid")]
        indexes = [models.Index(fields=["series", "period_start", "period_end", "quality_status"])]
    def clean(self):
        if self.source_record_id and self.source_record.organization_id != self.series.organization_id:
            raise ValidationError({"source_record": "Metric source must share series organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    @classmethod
    def current_accepted(cls, series, period_start, period_end):
        return cls.objects.filter(series=series, period_start=period_start, period_end=period_end, quality_status="accepted").order_by("-revision", "-observed_at", "-id").first()


class ReportImport(UUIDTimestamped):
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT)
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT)
    asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT)
    file_hash = models.CharField(max_length=64)
    report_type = models.CharField(max_length=60)
    coverage_start = models.DateField(null=True, blank=True)
    coverage_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="pending")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["artist", "service", "file_hash"], name="report_file_unique")]
    def clean(self):
        if self.artist_id and self.asset_id:
            validate_asset_ownership(self.asset, self.artist.organization_id)
            if self.asset.visibility != "private":
                raise ValidationError({"asset": "Report import must use a private asset."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AudioAnalysis(UUIDTimestamped):
    recording = models.ForeignKey("catalog.Recording", on_delete=models.CASCADE)
    input_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT)
    engine = models.CharField(max_length=100)
    engine_version = models.CharField(max_length=80)
    tempo = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    key = models.CharField(max_length=30, blank=True)
    loudness = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    features_json = models.JSONField(default=dict)
    analyzed_at = models.DateTimeField()
    def clean(self):
        if self.recording_id and self.input_asset_id:
            validate_asset_ownership(self.input_asset, self.recording.organization_id)
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PlaylistAppearance(UUIDTimestamped):
    recording = models.ForeignKey("catalog.Recording", on_delete=models.CASCADE)
    playlist_object = models.ForeignKey("catalog.PlatformObject", on_delete=models.PROTECT)
    first_observed_at = models.DateTimeField()
    last_observed_at = models.DateTimeField()
    position = models.PositiveIntegerField(null=True, blank=True)
    source_record = models.ForeignKey("provenance.SourceRecord", on_delete=models.PROTECT, null=True, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(last_observed_at__gte=models.F("first_observed_at")), name="playlist_observation_dates_valid")]
    def clean(self):
        if self.recording_id and self.source_record_id and self.recording.organization_id != self.source_record.organization_id:
            raise ValidationError({"source_record": "Playlist source must share recording organization."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
