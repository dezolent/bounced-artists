from django.test import TestCase
from django.utils import timezone
from artists.models import Artist, Organization, Service
from provenance.models import AuditEvent, EntityTarget, ReviewCandidate, ReviewItem, SourceRecord


class SocialReviewCleanupTests(TestCase):
    def test_only_artist_only_social_discovery_reviews_are_dismissed(self):
        from provenance.social_review_cleanup import plan_social_reviews, apply_social_reviews
        org = Organization.objects.get(slug='bounced-studio')
        artist = Artist.objects.create(organization=org, name='Artist', slug='cleanup-artist')
        service = Service.objects.create(name='TikTok', slug='tiktok')
        source = SourceRecord.objects.create(organization=org, service=service, collector='fixture', collection_method='scraper',
            object_type='tiktok-video', external_id='123', observed_at=timezone.now(), payload_hash='a'*64, payload_json={'id':'123'})
        issue = ReviewItem.objects.create(source_record=source, issue_type='platform_object_discovered')
        ReviewCandidate.objects.create(review_item=issue, target=EntityTarget.objects.create(organization=org, artist=artist))
        other = ReviewItem.objects.create(source_record=source, issue_type='recording_identity_ambiguous')
        plan = plan_social_reviews()
        self.assertEqual(plan['count'], 1)
        self.assertTrue(apply_social_reviews(plan, rollback=True)['rolled_back'])
        issue.refresh_from_db()
        self.assertEqual(issue.status, 'open')
        self.assertFalse(AuditEvent.objects.filter(action='review.dismiss_social_discovery').exists())
        apply_social_reviews(plan)
        issue.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((issue.status, other.status), ('dismissed', 'open'))
        self.assertEqual(AuditEvent.objects.filter(action='review.dismiss_social_discovery').count(), 1)
        self.assertTrue(SourceRecord.objects.filter(pk=source.pk).exists())
        with self.assertRaises(ValueError):
            apply_social_reviews(plan)
