"""Saving remains private; one explicit publication step replaces manual approval flags."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization
from catalog.models import Recording
from publishing.models import Website, WebsiteApprovedState, ApprovedCatalogRevision, PublicationOutbox
from portal.models import CatalogReviewRequest


class PublicationWorkflowTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Publish artist',slug='publish-artist',management_status='managed')
        self.song=Recording.objects.create(organization=self.org,title='Draft song')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song,capability='manage')
        self.site=Website.objects.create(organization=self.org,artist=self.artist,domain='publish.example.test')
        self.owner=get_user_model().objects.create_user(username='publisher')
        ArtistMembership.objects.create(artist=self.artist,user=self.owner,role='owner')
        self.staff=get_user_model().objects.create_user(username='publish-staff',is_staff=True)
        self.client.force_login(self.owner)
        self.url=f'/app/artists/{self.artist.pk}/publish/'

    def preview(self,**extra):
        data={'action':'preview','website':str(self.site.pk),'recordings':[str(self.song.pk)],**extra}
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['publication_token'])
        return response.context['publication_token']

    def test_owner_publishes_draft_in_one_portal_workflow_and_later_edits_stay_private(self):
        token=self.preview()
        self.assertFalse(WebsiteApprovedState.objects.exists())
        response=self.client.post(self.url,{'action':'publish','publication_token':token})
        self.assertRedirects(response,self.url,fetch_redirect_response=False)
        self.song.refresh_from_db()
        self.assertEqual(self.song.status,'approved')
        self.assertEqual(ApprovedCatalogRevision.objects.get().approver,self.owner)
        self.assertEqual(self.client.get('/api/public/v1/artists/publish-artist/recordings/').json()['results'][0]['title'],'Draft song')
        self.song.title='Private edit';self.song.save()
        self.assertEqual(self.client.get('/api/public/v1/artists/publish-artist/recordings/').json()['results'][0]['title'],'Draft song')
        self.assertFalse(PublicationOutbox.objects.exists())

    def test_optional_staff_review_queues_without_publication_and_staff_can_publish(self):
        self.artist.requires_staff_review=True;self.artist.save()
        token=self.preview()
        response=self.client.post(self.url,{'action':'request-review','publication_token':token})
        self.assertEqual(response.status_code,302)
        self.assertEqual(CatalogReviewRequest.objects.get().status,'pending')
        self.assertFalse(WebsiteApprovedState.objects.exists())
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,403)
        self.client.force_login(self.staff)
        token=self.preview()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
        self.assertEqual(CatalogReviewRequest.objects.get().status,'resolved')

    def test_publish_rejects_stale_catalog_policy_and_removed_membership(self):
        token=self.preview()
        self.song.title='Changed';self.song.save()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,409)
        self.assertFalse(WebsiteApprovedState.objects.exists())
        token=self.preview()
        self.artist.requires_staff_review=True;self.artist.save()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,403)
        self.artist.requires_staff_review=False;self.artist.save()
        token=self.preview()
        ArtistMembership.objects.filter(user=self.owner,artist=self.artist).delete()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,404)

    def test_viewer_other_workspace_and_forged_selection_denied(self):
        ArtistMembership.objects.filter(user=self.owner).update(role='viewer')
        self.assertEqual(self.client.get(self.url).status_code,403)
        ArtistMembership.objects.filter(user=self.owner).update(role='owner')
        unassigned=Recording.objects.create(organization=self.org,title='Not assigned')
        self.assertEqual(self.client.post(self.url,{'action':'preview','website':self.site.pk,'recordings':[str(unassigned.pk)]}).status_code,400)
        self.assertFalse(WebsiteApprovedState.objects.exists())

    def test_only_staff_configures_review_policy_and_owner_can_withdraw_publication(self):
        settings_url=f'/app/artists/{self.artist.pk}/publication-settings/'
        self.assertEqual(self.client.get(settings_url).status_code,403)
        token=self.preview()
        self.client.post(self.url,{'action':'publish','publication_token':token})
        page=self.client.get(self.url)
        response=self.client.post(self.url,{'action':'withdraw','withdraw_token':page.context['withdraw_token']})
        self.assertEqual(response.status_code,302)
        self.assertIsNone(WebsiteApprovedState.objects.get().active_revision_id)

    def test_csrf_and_invalid_action_cannot_publish(self):
        from django.test import Client
        csrf_client=Client(enforce_csrf_checks=True);csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url,{'action':'preview'}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'action':'approve'}).status_code,400)

    def test_publication_accepts_safe_pending_destinations_without_accepting_rejected_or_unsafe_links(self):
        from artists.models import Service
        from catalog.models import PlatformObject,RecordingPlatformMatch
        service=Service.objects.create(name='Spotify publication',slug='spotify',service_type='streaming')
        matches=[]
        for url,status in [('https://open.spotify.com/track/123','pending'),('https://open.spotify.com/track/rejected','rejected'),('https://unrelated.example.test/private','pending')]:
            platform=PlatformObject.objects.create(service=service,object_type='recording',url=url)
            matches.append(RecordingPlatformMatch.objects.create(recording=self.song,platform_object=platform,review_status=status))
        token=self.preview()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
        for item in matches: item.refresh_from_db()
        self.assertEqual([item.review_status for item in matches],['accepted','rejected','pending'])
        links=ApprovedCatalogRevision.objects.get().payload['recordings'][0]['platform_links']
        self.assertEqual([row['url'] for row in links],['https://open.spotify.com/track/123'])

    def test_publishing_release_promotes_its_group_and_includes_tracks(self):
        from catalog.models import ReleaseGroup,Release,ReleaseTrack
        group=ReleaseGroup.objects.create(organization=self.org,title='Draft album group')
        release=Release.objects.create(organization=self.org,title='Draft album',release_group=group)
        ReleaseTrack.objects.create(release=release,recording=self.song,track_number=1)
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='manage')
        ArtistResourceGrant.objects.create(artist=self.artist,release_group=group,capability='manage')
        token=self.preview(releases=[str(release.pk)])
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
        group.refresh_from_db();release.refresh_from_db()
        self.assertEqual((group.status,release.status),('approved','approved'))
        self.assertEqual(len(ApprovedCatalogRevision.objects.get().payload['releases'][0]['tracks']),1)

    def test_lyrics_need_explicit_rights_confirmation_and_publication_uses_stored_text(self):
        import hashlib,tempfile
        from django.core.files.base import ContentFile
        from django.test import override_settings
        from catalog.models import Asset,AssetVersion,RecordingAsset
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory):
            raw=b'Synthetic lyric preview line'
            asset=Asset.objects.create(organization=self.org,storage_key='pending/test-lyrics',display_name='Lyric sheet',category='lyrics_text',rights_basis='Artist owned')
            version=AssetVersion.objects.create(asset=asset,file=ContentFile(raw,name='lyrics.txt'),original_name='lyrics.txt',byte_size=len(raw),mime_type='text/plain',sha256=hashlib.sha256(raw).hexdigest(),creator=self.owner)
            asset.current_version=version;asset.save()
            RecordingAsset.objects.create(recording=self.song,asset=asset,purpose='lyrics')
            ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='manage')
            data={'action':'preview','website':str(self.site.pk),'recordings':[str(self.song.pk)],'lyrics':[str(asset.pk)]}
            self.assertEqual(self.client.post(self.url,data).status_code,400)
            asset.refresh_from_db();self.assertFalse(asset.rights_approved)
            token=self.preview(lyrics=[str(asset.pk)],confirm_rights='on')
            self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
            asset.refresh_from_db()
            self.assertEqual((asset.visibility,asset.rights_approved),('public',True))
            self.assertEqual(ApprovedCatalogRevision.objects.get().payload['recordings'][0]['lyrics'][0]['text'],raw.decode())

    def test_lost_resource_grant_rejects_preview_and_promotion_failure_rolls_back(self):
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        token=self.preview()
        with patch('portal.publication.store_revision',side_effect=ValidationError('Cannot publish this version.')):
            self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,400)
        self.song.refresh_from_db();self.assertEqual(self.song.status,'draft')
        self.assertFalse(WebsiteApprovedState.objects.exists())
        ArtistResourceGrant.objects.filter(artist=self.artist,recording=self.song).delete()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,400)

    def test_policy_settings_are_audited_and_reject_stale_or_forged_updates(self):
        from provenance.models import AuditEvent
        self.client.force_login(self.staff)
        url=f'/app/artists/{self.artist.pk}/publication-settings/'
        page=self.client.get(url)
        version=page.context['form']['version'].value()
        self.assertEqual(self.client.post(url,{'version':version,'requires_staff_review':'on','is_staff':'on'}).status_code,400)
        self.assertEqual(self.client.post(url,{'version':version,'requires_staff_review':'on'}).status_code,302)
        self.assertTrue(AuditEvent.objects.filter(action='portal.publication.policy').exists())
        self.assertEqual(self.client.post(url,{'version':version}).status_code,409)

    def test_publishing_after_another_revision_requires_a_fresh_preview(self):
        self.song.status='approved';self.song.save()
        token=self.preview()
        competing=self.preview()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':competing}).status_code,409)
        self.assertEqual(ApprovedCatalogRevision.objects.count(),1)

    def test_view_only_release_group_cannot_be_promoted_by_publishing_a_release(self):
        from catalog.models import ReleaseGroup,Release
        group=ReleaseGroup.objects.create(organization=self.org,title='Shared draft group')
        release=Release.objects.create(organization=self.org,title='Managed release',release_group=group)
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='manage')
        ArtistResourceGrant.objects.create(artist=self.artist,release_group=group,capability='read')
        response=self.client.post(self.url,{'action':'preview','website':self.site.pk,'releases':[str(release.pk)]})
        self.assertEqual(response.status_code,400)
        group.refresh_from_db();self.assertEqual(group.status,'draft')

    def test_identical_urls_on_different_services_do_not_accept_an_unpreviewed_match(self):
        from artists.models import Service
        from catalog.models import PlatformObject,RecordingPlatformMatch
        spotify=Service.objects.create(name='Spotify publication',slug='spotify',service_type='streaming')
        youtube=Service.objects.create(name='YouTube publication',slug='youtube',service_type='streaming')
        url='https://open.spotify.com/track/123'
        matches=[]
        for service in (spotify,youtube):
            platform=PlatformObject.objects.create(service=service,object_type='recording',url=url)
            matches.append(RecordingPlatformMatch.objects.create(recording=self.song,platform_object=platform))
        token=self.preview()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':token}).status_code,302)
        for item in matches: item.refresh_from_db()
        self.assertEqual([item.review_status for item in matches],['accepted','pending'])

    def test_staff_preview_cannot_resolve_a_later_changed_review_request(self):
        self.artist.requires_staff_review=True;self.artist.save()
        token=self.preview()
        self.client.post(self.url,{'action':'request-review','publication_token':token})
        self.client.force_login(self.staff)
        staff_token=self.preview()
        review=CatalogReviewRequest.objects.get()
        review.publication_selection={**review.publication_selection,'recordings':[]}
        review.save()
        self.assertEqual(self.client.post(self.url,{'action':'publish','publication_token':staff_token}).status_code,409)
        review.refresh_from_db();self.assertEqual(review.status,'pending')
        self.assertFalse(WebsiteApprovedState.objects.exists())
