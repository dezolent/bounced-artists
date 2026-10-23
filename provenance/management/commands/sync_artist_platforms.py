import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from provenance.collectors.http import CollectionError
from provenance.collectors.runner import JOB_TYPE, run_due
from provenance.models import SyncJob


class Command(BaseCommand):
    help = "Collect due artist platform jobs once, then exit. Default is a no-network, no-write preview."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--force", action="store_true", help="Repeat completed jobs, preserving same-day snapshot deduplication.")
        parser.add_argument("--artist", type=uuid.UUID)
        parser.add_argument("--request-limit", type=int, default=1000, help="Total HTTP attempts, including retries and OAuth, per invocation.")
        parser.add_argument("--job-limit", type=int, default=250)

    def handle(self, *args, **options):
        if not options["apply"]:
            jobs = SyncJob.objects.filter(job_type=JOB_TYPE, enabled=True)
            if options["artist"]:
                jobs = jobs.filter(connection__artist_id=options["artist"])
            self.stdout.write(json.dumps({"apply": False, "enabled_jobs": jobs.count(), "request_limit": options["request_limit"]}))
            return
        try:
            summary = run_due(request_limit=options["request_limit"], job_limit=options["job_limit"],
                              artist=options["artist"], force=options["force"])
        except CollectionError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(summary, sort_keys=True))
        if summary["failed"]:
            raise CommandError("Platform collection failed; inspect private SyncRun receipts.")
