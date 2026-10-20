from django.db import transaction
from django.db.models import Max
from .models import Page, PublicationOutbox, Website


@transaction.atomic
def approve_page_and_request_publication(page_id, idempotency_key):
    if not idempotency_key:
        raise ValueError("A stable change key is required")
    page = Page.objects.select_related("website").get(pk=page_id)
    # Lock the website row even when its outbox is empty so versions serialize.
    website = Website.objects.select_for_update().get(pk=page.website_id)
    existing = PublicationOutbox.objects.filter(website=website, idempotency_key=idempotency_key).first()
    if existing:
        return existing
    page.publication_status = "approved"
    page.save(update_fields=["publication_status", "updated_at"])
    max_version = PublicationOutbox.objects.filter(website=website).aggregate(Max("content_version"))["content_version__max"] or 0
    return PublicationOutbox.objects.create(website=website, content_version=max_version + 1, idempotency_key=idempotency_key)
