from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization
from catalog.models import Recording
from portal.models import CatalogReviewRequest
from provenance.models import AuditEvent, EntityTarget, FieldObservation, ReviewCandidate, ReviewItem, SourceRecord


class ReviewQueueTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Queue artist',slug='queue-artist',management_status='managed')
        self.song=Recording.objects.create(organization=self.org,title='Candidate song')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song,capability='manage')
        self.target=EntityTarget.objects.create(organization=self.org,recording=self.song)
        self.source=SourceRecord.objects.create(organization=self.org,collector='Queue collector',collection_method='manual',object_type='recording',external_id='source-123',payload_hash='a'*64,payload_json={'title':'Evidence title','private_notes':'Do not show raw body'},observed_at=timezone.now())
        self.issue=ReviewItem.objects.create(source_record=self.source,issue_type='ambiguous_recording')
        ReviewCandidate.objects.create(review_item=self.issue,target=self.target)
        FieldObservation.objects.create(source_record=self.source,target=self.target,field_name='title',value_json='Observed song')
        self.request=CatalogReviewRequest.objects.create(artist=self.artist)
        self.staff=get_user_model().objects.create_user(username='queue-staff',is_staff=True)
        self.member=get_user_model().objects.create_user(username='queue-member')
        ArtistMembership.objects.create(artist=self.artist,user=self.member,role='owner')
        self.client.force_login(self.staff)

    def issue_url(self):
        return reverse('portal:review-issue',args=[self.issue.pk])

    def request_url(self):
        return reverse('portal:review-request',args=[self.request.pk])

    def submit(self,url,action='resolve',notes='Reviewed the source and corrected the catalog.',**extra):
        response=self.client.get(url)
        return self.client.post(url,{'version':response.context['form']['version'].value(),'action':action,'notes':notes,**extra})

    def test_queue_and_details_are_staff_only(self):
        urls=[reverse('portal:review-queue'),self.issue_url(),self.request_url()]
        self.client.force_login(self.member)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url,{}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(urls[0]).status_code,302)

    def test_inbox_search_filters_and_both_types(self):
        url=reverse('portal:review-queue')
        response=self.client.get(url)
        self.assertContains(response,'Candidate song')
        self.assertContains(response,'Queue artist')
        self.assertContains(response,'Queue collector')
        self.assertEqual(response.context['page_obj'].paginator.count,2)
        self.assertEqual(self.client.get(url,{'q':'source-123'}).context['page_obj'].paginator.count,1)
        self.assertEqual(self.client.get(url,{'q':'Queue artist'}).context['page_obj'].paginator.count,2)
        self.assertEqual(self.client.get(url,{'kind':'request'}).context['page_obj'].paginator.count,1)
        self.assertEqual(self.client.get(url,{'status':'resolved'}).context['page_obj'].paginator.count,0)
        self.assertEqual(self.client.get(url,{'artist':str(self.artist.pk)}).context['page_obj'].paginator.count,2)
        self.assertEqual(self.client.get(url,{'status':'unknown'}).status_code,400)

    def test_detail_has_safe_evidence_and_actionable_candidate(self):
        response=self.client.get(self.issue_url())
        self.assertContains(response,'Evidence title')
        self.assertContains(response,'Observed song')
        self.assertNotContains(response,'Do not show raw body')
        self.assertContains(response,reverse('portal:recording',args=[self.artist.pk,self.song.pk]))
        self.assertContains(response,'omitted')

    def test_resolution_audits_actor_and_notes_without_editing_catalog(self):
        response=self.submit(self.issue_url())
        self.assertEqual(response.status_code,302)
        self.issue.refresh_from_db();self.song.refresh_from_db()
        self.assertEqual(self.issue.status,'resolved')
        self.assertEqual(self.issue.resolved_by,self.staff)
        self.assertEqual(self.song.title,'Candidate song')
        audit=AuditEvent.objects.get(action='portal.review_issue')
        self.assertEqual(audit.user,self.staff)
        self.assertEqual(audit.organization,self.org)
        self.assertEqual(audit.change_json['previous_status'],'open')
        self.assertIn('corrected',audit.change_json['notes'])
        self.assertEqual(self.submit(self.issue_url(),action='reopen').status_code,302)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status,'open');self.assertIsNone(self.issue.resolved_by)
        self.assertEqual(AuditEvent.objects.filter(action='portal.review_issue').count(),2)

    def test_stale_source_candidate_or_status_prevents_resolution(self):
        for change in ['source','candidate','status']:
            response=self.client.get(self.issue_url())
            token=response.context['form']['version'].value()
            if change=='source':
                self.source.parser_version='new-parser';self.source.save()
            elif change=='candidate':
                self.song.title='Edited candidate';self.song.save()
            else:
                self.issue.status='dismissed';self.issue.save()
            response=self.client.post(self.issue_url(),{'version':token,'action':'resolve','notes':'Checked.'})
            self.assertEqual(response.status_code,409)
        self.assertFalse(AuditEvent.objects.filter(action='portal.review_issue').exists())

    def test_invalid_action_extra_candidate_and_empty_note_do_not_write(self):
        for extra in [{'action':'publish'},{'candidate':str(self.target.pk)},{'status':'resolved'},{'notes':''}]:
            self.assertEqual(self.submit(self.issue_url(),**extra).status_code,400)
        self.issue.refresh_from_db();self.assertEqual(self.issue.status,'open')
        self.assertFalse(AuditEvent.objects.exists())

    def test_signed_token_belongs_to_actor(self):
        token=self.client.get(self.issue_url()).context['form']['version'].value()
        other=get_user_model().objects.create_user(username='other-staff',is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.issue_url(),{'version':token,'action':'resolve','notes':'Checked'}).status_code,409)

    def test_csrf_required(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.staff)
        self.assertEqual(client.post(self.issue_url(),{'action':'resolve'}).status_code,403)

    def test_request_changes_remains_separate_from_publication(self):
        response=self.client.get(self.request_url())
        self.assertContains(response,reverse('portal:publish',args=[self.artist.pk]))
        self.assertEqual(self.submit(self.request_url(),action='resolve').status_code,400)
        self.assertEqual(self.submit(self.request_url(),action='return',notes='Please correct the release dates.').status_code,302)
        self.request.refresh_from_db();self.assertEqual(self.request.status,'returned')
        audit=AuditEvent.objects.get(action='portal.review_request')
        self.assertEqual(audit.change_json['notes'],'Please correct the release dates.')
        self.assertEqual(self.submit(self.request_url(),action='reopen').status_code,302)
        self.request.refresh_from_db();self.assertEqual(self.request.status,'pending')

    def test_corrupt_cross_organization_candidate_is_not_followed_or_resolved(self):
        other=Organization.objects.create(name='Other review org',slug='other-review-org')
        target=EntityTarget.objects.create(organization=other,artist=Artist.objects.create(organization=other,name='Foreign hidden artist',slug='foreign-review-artist'))
        ReviewCandidate.objects.filter(review_item=self.issue).update(target=target)
        response=self.client.get(self.issue_url())
        self.assertNotContains(response,'Foreign hidden artist')
        self.assertContains(response,'organization mismatch')
        self.assertEqual(self.submit(self.issue_url()).status_code,400)

    def test_secret_payload_fields_and_embedded_credentials_are_redacted(self):
        SourceRecord.objects.filter(pk=self.source.pk).update(payload_json={'title':'Authorization: Bearer should-never-appear','api_key':'should-never-appear','name':'Safe name'})
        FieldObservation.objects.filter(source_record=self.source).update(value_json={'password':'should-never-appear'})
        response=self.client.get(self.issue_url())
        self.assertNotContains(response,'should-never-appear')
        self.assertContains(response,'Safe name')
        self.assertContains(response,'redacted')

    def test_staff_privilege_rechecked_under_transaction(self):
        from portal.review_queue import decide
        token=self.client.get(self.issue_url()).context['form']['version'].value()
        get_user_model().objects.filter(pk=self.staff.pk).update(is_staff=False)
        with self.assertRaises(PermissionDenied):
            decide(self.staff,'issue',self.issue.pk,{'version':token,'action':'resolve','notes':'Checked.'})
        self.issue.refresh_from_db();self.assertEqual(self.issue.status,'open')
        self.assertFalse(AuditEvent.objects.exists())

    def test_request_selection_change_invalidates_decision(self):
        token=self.client.get(self.request_url()).context['form']['version'].value()
        CatalogReviewRequest.objects.filter(pk=self.request.pk).update(publication_selection={'recordings':[str(self.song.pk)]})
        response=self.client.post(self.request_url(),{'version':token,'action':'return','notes':'Check current titles.'})
        self.assertEqual(response.status_code,409)
        self.request.refresh_from_db();self.assertEqual(self.request.status,'pending')

    def test_cross_organization_publication_website_rejected(self):
        from publishing.models import Website
        other=Organization.objects.create(name='Request other org',slug='request-other-org')
        artist=Artist.objects.create(organization=other,name='Other requested artist',slug='other-requested-artist')
        site=Website.objects.create(organization=other,artist=artist,domain='request-other.example.test')
        self.request.website=site
        with self.assertRaises(ValidationError):
            self.request.full_clean()

    def test_unassigned_candidate_links_staff_operations(self):
        ArtistResourceGrant.objects.filter(recording=self.song).delete()
        response=self.client.get(self.issue_url())
        self.assertContains(response,reverse('portal:operation-detail',kwargs={'key':'catalog-recording','object_id':str(self.song.pk)}))

    def test_reopen_returned_request_does_not_override_current_pending(self):
        self.request.status='returned';self.request.save()
        active=CatalogReviewRequest.objects.create(artist=self.artist)
        self.assertEqual(self.submit(self.request_url(),action='reopen').status_code,400)
        self.request.refresh_from_db();active.refresh_from_db()
        self.assertEqual(self.request.status,'returned');self.assertEqual(active.status,'pending')

    def test_dismiss_records_current_resolver_and_replay_is_stale(self):
        token=self.client.get(self.issue_url()).context['form']['version'].value()
        response=self.client.post(self.issue_url(),{'version':token,'action':'dismiss','notes':'Duplicate source issue; catalog evidence already preserved.'})
        self.assertEqual(response.status_code,302)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status,'dismissed');self.assertEqual(self.issue.resolved_by,self.staff)
        self.assertEqual(self.client.post(self.issue_url(),{'version':token,'action':'dismiss','notes':'Retry'}).status_code,409)
        self.assertEqual(AuditEvent.objects.filter(action='portal.review_issue').count(),1)

    def test_return_reopen_cycle_invalidates_original_request_token(self):
        token=self.client.get(self.request_url()).context['form']['version'].value()
        self.assertEqual(self.submit(self.request_url(),action='return').status_code,302)
        self.assertEqual(self.submit(self.request_url(),action='reopen').status_code,302)
        self.assertEqual(self.client.post(self.request_url(),{'version':token,'action':'return','notes':'Retry old review'}).status_code,409)
        self.request.refresh_from_db();self.assertEqual(self.request.status,'pending')

    def test_long_nested_metadata_is_bounded_and_html_escaped(self):
        self.source.payload_json={'title':'<script>unsafe()</script>','artists':[{'name':'X'*10000} for _ in range(100)],'body':'do-not-render-body'*10000}
        self.source.save()
        response=self.client.get(self.issue_url())
        self.assertContains(response,'&lt;script&gt;unsafe()&lt;/script&gt;')
        self.assertNotContains(response,'<script>unsafe()</script>')
        self.assertNotContains(response,'do-not-render-body')
        self.assertLess(len(response.content),50000)

    def test_unknown_persisted_issue_status_cannot_be_rewritten(self):
        ReviewItem.objects.filter(pk=self.issue.pk).update(status='unexpected')
        self.assertEqual(self.submit(self.issue_url(),action='reopen').status_code,400)
        self.issue.refresh_from_db();self.assertEqual(self.issue.status,'unexpected')
        self.assertFalse(AuditEvent.objects.exists())
