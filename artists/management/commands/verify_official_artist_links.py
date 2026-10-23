import json
from django.core.management.base import BaseCommand
from artists.official_links import load_manifest, verify_links


class Command(BaseCommand):
    help = "Verify the three customer workspaces' official website links; default previews without writes."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(verify_links(load_manifest(), apply=options["apply"]), sort_keys=True))
