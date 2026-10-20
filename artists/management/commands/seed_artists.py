from django.core.management.base import BaseCommand
from django.db import transaction
from artists.models import Artist


class Command(BaseCommand):
    help = "Create missing MVP artists without overwriting existing information."

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        for name, slug in (("Dezolent", "dezolent"), ("SRY4U", "sry4u"), ("Morbeato", "morbeato")):
            if Artist.objects.filter(bootstrap_key=slug).exists():
                continue
            existing = Artist.objects.filter(slug__iexact=slug).first()
            if existing is not None:
                # Add internal provenance only; preserve every user-editable field.
                Artist.objects.filter(pk=existing.pk).update(bootstrap_key=slug)
            else:
                Artist.objects.create(bootstrap_key=slug, slug=slug, name=name, management_status=Artist.ManagementStatus.MANAGED)
                created_count += 1
        self.stdout.write(self.style.SUCCESS(f"MVP artists: {created_count} created, {3 - created_count} preserved."))
