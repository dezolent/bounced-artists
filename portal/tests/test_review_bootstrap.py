import io
import json
import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization
from catalog.models import Recording
from publishing.models import Website, WebsiteApprovedState, PublicationOutbox
from portal.models import CatalogReviewRequest


class ReviewBootstrapTests(TestCase):
    def setUp(self):
        org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=org,name='Reviewer Artist',slug='reviewer-artist')
        self.song=Recording.objects.create(organization=org,title='Review me',status='approved')
        self.site=Website.objects.create(organization=org,artist=self.artist,domain='review.example.test',theme_config={'dezolent_import':{'entity_ids':{'recording:exact-key':str(self.song.pk)}}})
        self.owner=get_user_model().objects.create_user(username='owner',password='test-password')
        ArtistMembership.objects.create(artist=self.artist,user=self.owner,role='owner')
        self.staff=get_user_model().objects.create_user(username='operator',password='test-password',is_staff=True)
        self.url=reverse('portal:review',args=[self.artist.pk,self.site.pk])

    def test_dry_run_has_no_grants_then_reviewed_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            report=str(Path(temp)/'report.json')
            call_command('assign_import_catalog',website=self.site.domain,artist=str(self.artist.pk),report=report,stdout=io.StringIO())
            self.assertFalse(ArtistResourceGrant.objects.exists())
            document=json.loads(Path(report).read_text())
            self.assertEqual(document['grants'],[{'kind':'recording','id':str(self.song.pk)}])
            with self.assertRaises(CommandError):
                call_command('assign_import_catalog',website=self.site.domain,artist=str(self.artist.pk),report=report,apply=True,expected_sha256='wrong',stdout=io.StringIO())
            for _ in range(2):
                call_command('assign_import_catalog',website=self.site.domain,artist=str(self.artist.pk),report=report,apply=True,expected_sha256=document['sha256'],stdout=io.StringIO())
            self.assertEqual(ArtistResourceGrant.objects.filter(artist=self.artist,recording=self.song).count(),1)

    def test_owner_can_request_review_but_cannot_approve(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code,403)
        url=reverse('portal:request-review',args=[self.artist.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        for _ in range(2):
            self.assertEqual(self.client.post(url).status_code,302)
        self.assertEqual(CatalogReviewRequest.objects.filter(artist=self.artist,status='pending').count(),1)
        self.assertFalse(WebsiteApprovedState.objects.exists())

    def test_persisted_remote_asset_mappings_are_explicitly_assigned_too(self):
        from catalog.models import Asset
        asset=Asset.objects.create(organization=self.artist.organization,source_url='https://images.example.test/cover.png')
        self.site.theme_config['dezolent_import']['entity_ids']['remote_asset:exact-key']=str(asset.pk)
        self.site.save()
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'grants.json')
            call_command('assign_import_catalog',website=self.site.domain,artist=str(self.artist.pk),report=path,stdout=io.StringIO())
            report=json.loads(Path(path).read_text())
            self.assertIn({'kind':'asset','id':str(asset.pk)},report['grants'])

    def preview(self,expected_title='Review me'):
        ArtistResourceGrant.objects.get_or_create(artist=self.artist,recording=self.song,defaults={'capability':'manage'})
        self.client.force_login(self.staff)
        response=self.client.post(self.url,{'action':'preview','recordings':[str(self.song.pk)]})
        self.assertEqual(response.status_code,200,response.content)
        self.assertContains(response,expected_title)
        return response.context['review_token']

    def test_approval_requires_unchanged_signed_preview_and_never_enqueues_delivery(self):
        token=self.preview()
        self.song.title='Changed after review';self.song.save()
        response=self.client.post(self.url,{'action':'approve','review_token':token})
        self.assertEqual(response.status_code,409)
        self.assertFalse(WebsiteApprovedState.objects.exists())
        token=self.preview('Changed after review')
        response=self.client.post(self.url,{'action':'approve','review_token':token})
        self.assertEqual(response.status_code,302,response.content)
        self.assertIsNotNone(WebsiteApprovedState.objects.get(website=self.site).active_revision_id)
        self.assertFalse(PublicationOutbox.objects.exists())
        self.assertEqual(self.client.get('/api/public/v1/artists/reviewer-artist/recordings/').json()['results'][0]['title'],'Changed after review')
        self.assertEqual(self.client.post(self.url,{'action':'revoke'}).status_code,302)
        self.assertEqual(self.client.get('/api/public/v1/artists/reviewer-artist/').status_code,404)

    def test_unrelated_private_delete_does_not_withdraw_approved_catalog(self):
        from artists.models import ArtistContact
        from portal.forms import stamp
        from rest_framework.test import APIClient
        token=self.preview()
        self.client.post(self.url,{'action':'approve','review_token':token})
        contact=ArtistContact.objects.create(artist=self.artist,value='member-only@example.test',visibility='members')
        api=APIClient();api.force_authenticate(self.owner)
        response=api.delete(f'/api/portal/v1/artists/{self.artist.pk}/contacts/{contact.pk}/',{'version':stamp(contact)},format='json')
        self.assertEqual(response.status_code,204)
        self.assertEqual(self.client.get('/api/public/v1/artists/reviewer-artist/').status_code,200)
