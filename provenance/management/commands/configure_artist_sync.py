import json

from django.core.management.base import BaseCommand
from django.db import transaction

from provenance.collectors.http import CollectionError
from provenance.collectors.providers import profile_locator
from provenance.collectors.runner import JOB_TYPE, eligible_profiles
from provenance.models import Connection, SyncJob


class Command(BaseCommand):
    help = "Preview daily jobs for verified managed artist profiles; --apply writes configuration only."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        links = []
        identities = set()
        invalid = 0
        for link in eligible_profiles().order_by("created_at", "pk"):
            try:
                identity = (link.artist_id, link.platform_id, profile_locator(link.platform.slug, link.url))
                if identity in identities:
                    continue
                identities.add(identity)
                links.append(link)
            except (CollectionError, ValueError):
                invalid += 1
        wanted = {str(link.pk) for link in links}
        obsolete = [job.pk for job in SyncJob.objects.filter(job_type=JOB_TYPE, enabled=True)
                    if job.cursor_json.get("profile_link_id") not in wanted]
        if options["apply"]:
            SyncJob.objects.filter(pk__in=obsolete).update(enabled=False)
            for link in links:
                # Stable profile row, artist and organization are the identity.
                job = SyncJob.objects.filter(job_type=JOB_TYPE, connection__artist=link.artist,
                    connection__organization=link.artist.organization, connection__service=link.platform,
                    cursor_json__profile_link_id=str(link.pk)).first()
                if job:
                    # Preserve deliberate operator pauses and connection status.
                    continue
                conn = Connection.objects.create(organization=link.artist.organization, artist=link.artist,
                    service=link.platform, auth_type="api_key", secret_reference="env:OMKAR_API_KEY", status="active")
                SyncJob.objects.create(connection=conn, job_type=JOB_TYPE, schedule="0 9 * * *",
                    enabled=True, cursor_json={"profile_link_id": str(link.pk)})
        self.stdout.write(json.dumps({"apply": options["apply"], "eligible_profiles": len(links),
            "invalid_profiles": invalid, "jobs_to_disable": len(obsolete)}, sort_keys=True))
