from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from core.schema import UUIDTimestamped


class Venue(UUIDTimestamped):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=120, blank=True)
    region = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=3, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    def __str__(self): return self.name


class Event(UUIDTimestamped):
    organization = models.ForeignKey("artists.Organization", on_delete=models.PROTECT)
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, null=True, blank=True)
    title = models.CharField(max_length=300)
    local_date = models.DateField()
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    time_precision = models.CharField(max_length=20, default="date", choices=[("date", "Date only"), ("approximate", "Approximate"), ("exact", "Exact")])
    status = models.CharField(max_length=20, default="scheduled", choices=[("scheduled", "Scheduled"), ("cancelled", "Cancelled"), ("postponed", "Postponed")])
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gte=F("starts_at")), name="event_times_valid")]
    def __str__(self): return self.title


class EventArtist(UUIDTimestamped):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    artist = models.ForeignKey("artists.Artist", on_delete=models.PROTECT)
    billing_order = models.PositiveSmallIntegerField(default=1)
    performance_role = models.CharField(max_length=30, default="performer")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "artist", "performance_role"], name="event_artist_unique")]


class EventSource(UUIDTimestamped):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    service = models.ForeignKey("artists.Service", on_delete=models.PROTECT)
    external_id = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(max_length=2048, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    source_status = models.CharField(max_length=30, choices=[("unknown", "Unknown"), ("active", "Active"), ("cancelled", "Cancelled"), ("removed", "Removed")], default="unknown")
    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(external_id="") | ~Q(source_url=""), name="event_source_has_locator"), models.UniqueConstraint(fields=["service", "external_id"], condition=~Q(external_id=""), name="event_source_id_unique")]


class TicketOffer(UUIDTimestamped):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    event_source = models.ForeignKey(EventSource, on_delete=models.PROTECT, null=True, blank=True)
    url = models.URLField(max_length=2048)
    offer_type = models.CharField(max_length=30, default="ticket")
    status = models.CharField(max_length=20, choices=[("available", "Available"), ("sold_out", "Sold out"), ("cancelled", "Cancelled"), ("unavailable", "Unavailable")], default="available")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "url"], name="ticket_offer_unique")]
    def clean(self):
        if self.event_source_id and self.event_source.event_id != self.event_id:
            raise ValidationError({"event_source": "Event source must belong to this event."})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
