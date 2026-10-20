"""Import a verified Dezolent site export into the existing organization."""

import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from artists.imports.dezolent import DezolentImporter
from artists.imports.dezolent_bundle import Bundle


class Command(BaseCommand):
    help = "Verify and import a Dezolent export. Dry run and rollback by default."

    def add_arguments(self, parser):
        parser.add_argument("--bundle", required=True, help="Directory containing manifest.json and the verified export files")
        parser.add_argument("--apply", action="store_true", help="Commit an import with no editorial conflicts")

    def handle(self, *args, **options):
        try:
            bundle = Bundle(options["bundle"])
            report = DezolentImporter(bundle).run(apply=options["apply"])
        except (ValidationError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, sort_keys=True))
