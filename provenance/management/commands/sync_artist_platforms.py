import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from provenance.collectors.http import CollectionError
from provenance.collectors.backends import scraper_mode
from provenance.collectors.runner import JOB_TYPE, run_due
from provenance.models import SyncJob


class Command(BaseCommand):
    help = "Collect due artist platform jobs once, then exit. Default is a no-network, no-write preview."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--force", action="store_true", help="Refresh all endpoints, including reusable same-day details; preserve snapshot deduplication.")
        parser.add_argument("--artist", type=uuid.UUID)
        parser.add_argument("--request-limit", type=int, default=18000, help="Total HTTP attempts, including retries and OAuth, per invocation.")
        parser.add_argument("--job-request-limit", type=int, default=1000, help="Maximum HTTP attempts per profile; keeps large accounts from exhausting the invocation budget.")
        parser.add_argument("--time-limit", type=int, default=7200, help="Elapsed seconds allowed for the whole invocation.")
        parser.add_argument("--job-time-limit", type=int, default=300, help="Elapsed seconds per profile, checked between requests and saved rows.")
        parser.add_argument("--job-limit", type=int, default=250)

    def handle(self, *args, **options):
        if not options["apply"]:
            jobs = SyncJob.objects.filter(job_type=JOB_TYPE, enabled=True)
            if options["artist"]:
                jobs = jobs.filter(connection__artist_id=options["artist"])
            self.stdout.write(json.dumps({"apply": False, "scraper_backend":scraper_mode(), "enabled_jobs": jobs.count(), "request_limit": options["request_limit"], "job_request_limit": options["job_request_limit"]}))
            return
        def progress(event):
            level = "error" if event["event"] == "job_finished" and event.get("error_code") else "warn" if event.get("incomplete") else "info"
            platform = event.get("platform", "unknown")
            description = {"job_started": "started", "job_progress": "progress", "job_finished": event.get("status", "finished")}.get(event["event"], event["event"])
            message = f"{platform} collection {description}"
            if event.get("error_code"):
                message += f": {event['error_code']}"
            self.stdout.write(json.dumps({**event, "level": level, "message": message}, sort_keys=True))
            self.stdout.flush()

        try:
            summary = run_due(request_limit=options["request_limit"], job_request_limit=options["job_request_limit"], job_limit=options["job_limit"],
                              artist=options["artist"], force=options["force"], progress=progress,
                              time_limit=options["time_limit"], job_time_limit=options["job_time_limit"])
        except CollectionError as exc:
            raise CommandError(str(exc)) from None
        incomplete = any(summary.get(key) for key in ("failed", "skipped", "locked", "social_blocked", "time_limit_reached", "job_limit_reached"))
        self.stdout.write(json.dumps({**summary, "event": "sync_finished", "message": "Daily collection incomplete" if incomplete else "Daily collection completed successfully", "level": "error" if incomplete else "info"}, sort_keys=True))
        if incomplete:
            raise CommandError("Platform collection incomplete; inspect the structured job logs and private SyncRun receipts.")
