from django.core.management.base import BaseCommand
from django.db import transaction
from artists.models import Service
from rights.models import RightsSociety


SERVICES = (
    ("spotify", "Spotify", "music"), ("apple-music", "Apple Music", "music"),
    ("soundcloud", "SoundCloud", "music"), ("youtube", "YouTube", "video"),
    ("amazon-music", "Amazon Music", "music"), ("bandcamp", "Bandcamp", "music"),
    ("tidal", "Tidal", "music"), ("deezer", "Deezer", "music"),
    ("audiomack", "Audiomack", "music"), ("beatport", "Beatport", "music"),
    ("bandsintown", "Bandsintown", "events"), ("edmtrain", "EDMTrain", "events"),
)

SOCIETIES = (
    ("MLC", "The Mechanical Licensing Collective", "mechanical", "US"),
    ("ASCAP", "ASCAP", "performance", "US"),
    ("BMI", "BMI", "performance", "US"),
)


class Command(BaseCommand):
    help = "Create only established service references, preserving edits."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for slug, name, service_type in SERVICES:
            _, was_created = Service.objects.get_or_create(slug=slug, defaults={"name": name, "service_type": service_type})
            created += was_created
        self.stdout.write(f"Services: {created} created, {len(SERVICES) - created} preserved.")
        created = 0
        for code, name, society_type, territory in SOCIETIES:
            _, was_created = RightsSociety.objects.get_or_create(code=code, defaults={"name": name, "society_type": society_type, "territory": territory})
            created += was_created
        self.stdout.write(f"Rights societies: {created} created, {len(SOCIETIES) - created} preserved.")
