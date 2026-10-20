import io
import tempfile
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization, OrganizationMembership, ArtistContact
from catalog.models import Recording, RecordingArtist, RecordingIdentifier, Asset, AssetVersion
from portal.forms import stamp
from portal.models import EditorialChange, LoginThrottle


class WorkspaceTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Test Artist',slug='test-artist')
        self.other=Artist.objects.create(organization=self.org,name='Other Artist',slug='other-artist')
        self.user=get_user_model().objects.create_user(username='creator',password='test-password')
        self.membership=ArtistMembership.objects.create(artist=self.artist,user=self.user,role='owner')
        self.song=Recording.objects.create(organization=self.org,title='Visible Song')
        self.hidden=Recording.objects.create(organization=self.org,title='Hidden Song')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song,capability='manage')
        self.api=APIClient()
        self.api.force_authenticate(self.user)
        self.client.force_login(self.user)
        self.root=f'/api/portal/v1/artists/{self.artist.pk}'

    def test_membership_plus_grant_is_required_not_credits_or_org_membership(self):
        RecordingArtist.objects.create(recording=self.hidden,artist=self.artist)
        OrganizationMembership.objects.create(organization=self.org,user=self.user,role='owner')
        result=self.api.get(self.root+'/recordings/').json()
        self.assertEqual([row['title'] for row in result['results']],['Visible Song'])
        self.assertEqual(self.api.get(self.root+f'/recordings/{self.hidden.pk}/').status_code,404)
        self.assertEqual(self.api.get(f'/api/portal/v1/artists/{self.other.pk}/recordings/').status_code,404)
        self.membership.delete()
        self.assertEqual(self.api.get(self.root+'/recordings/').status_code,404)

    def test_read_grant_and_viewer_cannot_write(self):
        grant=ArtistResourceGrant.objects.get(artist=self.artist,recording=self.song)
        grant.capability='read';grant.save()
        response=self.api.patch(self.root+f'/recordings/{self.song.pk}/',{'version':stamp(self.song),'title':'Changed'},format='json')
        self.assertEqual(response.status_code,404)
        self.membership.role='viewer';self.membership.save()
        self.assertEqual(self.api.post(self.root+'/recordings/',{'title':'New'},format='json').status_code,403)

    def test_crud_preserves_original_identity_and_rejects_stale_and_hidden_fields(self):
        endpoint=self.root+f'/recordings/{self.song.pk}/'
        original=stamp(self.song)
        response=self.api.patch(endpoint,{'version':original,'title':'Edited'},format='json')
        self.assertEqual(response.status_code,200,response.content)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title,'Edited')
        self.assertEqual(self.api.patch(endpoint,{'version':original,'title':'Lost update'},format='json').status_code,409)
        self.assertEqual(self.api.patch(endpoint,{'version':stamp(self.song),'status':'approved'},format='json').status_code,400)
        response=self.api.delete(endpoint,{'version':stamp(self.song)},format='json')
        self.assertEqual(response.status_code,204,response.content)
        self.assertTrue(Recording.objects.filter(pk=self.song.pk).exists())
        self.assertEqual(self.api.get(endpoint).status_code,404)
        self.assertTrue(EditorialChange.objects.filter(action='delete',object_id=str(self.song.pk)).exists())

    def test_create_assigns_only_selected_artist_and_rejects_cross_artist_parent(self):
        response=self.api.post(self.root+'/recordings/',{'title':'New Song','recording_type':'original','date_precision':'unknown'},format='json')
        self.assertEqual(response.status_code,201,response.content)
        self.assertTrue(ArtistResourceGrant.objects.filter(artist=self.artist,recording_id=response.json()['id'],capability='manage').exists())
        result=self.api.post(self.root+'/recording-identifiers/',{'recording':str(self.hidden.pk),'scheme':'isrc','value':'NOT-OURS'},format='json')
        self.assertEqual(result.status_code,400,result.content)
        self.assertFalse(RecordingIdentifier.objects.filter(value='NOT-OURS').exists())

    def test_contact_visibility_and_legacy_private_api_remain_restricted(self):
        ArtistContact.objects.create(artist=self.artist,value='STAFF-ONLY-SENTINEL',visibility='private')
        ArtistContact.objects.create(artist=self.artist,value='member@example.test',visibility='members')
        response=self.api.get(self.root+'/contacts/')
        self.assertNotIn('STAFF-ONLY-SENTINEL',response.content.decode())
        self.assertEqual(response.json()['count'],1)
        self.assertEqual(self.api.patch(f'/api/v1/artists/{self.artist.pk}/',{'name':'Unsafe'},format='json').status_code,405)

    def test_tombstoned_child_is_hidden_but_source_row_survives(self):
        identifier=RecordingIdentifier.objects.create(recording=self.song,scheme='isrc',value='example')
        result=self.api.delete(self.root+f'/recording-identifiers/{identifier.pk}/',{'version':stamp(identifier)},format='json')
        self.assertEqual(result.status_code,204,result.content)
        self.assertTrue(RecordingIdentifier.objects.filter(pk=identifier.pk).exists())
        self.assertEqual(self.api.get(self.root+'/recording-identifiers/').json()['count'],0)

    def test_dashboard_journey_and_all_resource_forms_render(self):
        self.assertContains(self.client.get('/app/'),'Visible Song')
        self.assertNotContains(self.client.get('/app/'),'Hidden Song')
        self.assertContains(self.client.get(reverse('portal:recording',args=[self.artist.pk,self.song.pk])),'Visible Song')
        from portal.resources import RESOURCES
        for kind in RESOURCES:
            with self.subTest(kind=kind):
                self.assertEqual(self.client.get(reverse('portal:resources',args=[self.artist.pk,kind])).status_code,200)
                if kind!='artists':
                    self.assertEqual(self.client.get(reverse('portal:create',args=[self.artist.pk,kind])).status_code,200)

    def test_api_rejects_inactive_user_and_malformed_identifier(self):
        self.assertEqual(self.api.get(self.root+'/recordings/not-a-uuid/').status_code,404)
        self.user.is_active=False;self.user.save()
        self.assertIn(self.api.get(self.root+'/recordings/').status_code,(403,404))

    def test_workspace_writes_require_csrf(self):
        client=APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.root+'/recordings/',{'title':'Forged'}).status_code,403)

    def test_login_limiter_stops_repeated_password_attempts(self):
        self.client.logout()
        for _ in range(8):
            self.client.post('/',{'username':'creator','password':'wrong'})
        response=self.client.post('/',{'username':'creator','password':'test-password'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('_auth_user_id',self.client.session)
        self.assertEqual(LoginThrottle.objects.count(),1)

    def test_uploaded_artwork_has_private_original_and_sanitized_preview(self):
        from PIL import Image
        image=Image.new('RGB',(64,64),'purple')
        content=io.BytesIO();image.save(content,format='PNG')
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            upload=SimpleUploadedFile('../../cover.png',content.getvalue(),content_type='text/html')
            result=self.api.post(self.root+'/assets/',{'display_name':'Cover','visibility':'members','upload':upload},format='multipart')
            self.assertEqual(result.status_code,201,result.content)
            asset=Asset.objects.get(pk=result.json()['id'])
            self.assertEqual(asset.mime_type,'image/png')
            self.assertFalse(asset.rights_approved)
            self.assertTrue(asset.current_version.preview_file)
            response=self.client.get(reverse('portal:asset-media',args=[self.artist.pk,asset.pk,'preview']))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'image/webp')
            self.assertTrue(b''.join(response.streaming_content))
            ArtistResourceGrant.objects.filter(asset=asset).delete()
            self.assertEqual(self.client.get(reverse('portal:asset-media',args=[self.artist.pk,asset.pk,'download'])).status_code,404)

    def test_invalid_upload_rolls_back_asset_and_grant(self):
        upload=SimpleUploadedFile('unsafe.svg',b'<svg onload="alert(1)"></svg>',content_type='image/png')
        result=self.api.post(self.root+'/assets/',{'display_name':'Unsafe','visibility':'members','upload':upload},format='multipart')
        self.assertEqual(result.status_code,400,result.content)
        self.assertFalse(Asset.objects.filter(display_name='Unsafe').exists())

    def test_editor_cannot_archive_artist_workspace(self):
        self.membership.role='editor';self.membership.save()
        response=self.api.delete(self.root+f'/artists/{self.artist.pk}/',{'version':stamp(self.artist)},format='json')
        self.assertEqual(response.status_code,403)
        self.artist.refresh_from_db();self.assertIsNone(self.artist.archived_at)

    def test_identifier_edit_withdraws_its_verification(self):
        row=RecordingIdentifier.objects.create(recording=self.song,scheme='isrc',value='ORIGINAL',verification_status='verified')
        response=self.api.patch(self.root+f'/recording-identifiers/{row.pk}/',{'version':stamp(row),'value':'REPLACEMENT'},format='json')
        self.assertEqual(response.status_code,200,response.content)
        row.refresh_from_db();self.assertEqual(row.verification_status,'unverified')

    def test_deleted_identifier_can_be_recreated_without_losing_stable_uuid(self):
        row=RecordingIdentifier.objects.create(recording=self.song,scheme='isrc',value='SAME')
        self.assertEqual(self.api.delete(self.root+f'/recording-identifiers/{row.pk}/',{'version':stamp(row)},format='json').status_code,204)
        response=self.api.post(self.root+'/recording-identifiers/',{'recording':str(self.song.pk),'scheme':'isrc','value':'SAME'},format='json')
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(response.json()['id'],str(row.pk))
        self.assertEqual(self.api.get(self.root+'/recording-identifiers/').json()['count'],1)

    def test_secondary_record_grant_is_required_for_tracks_and_asset_links(self):
        from catalog.models import Release,ReleaseTrack,RecordingAsset
        release=Release.objects.create(organization=self.org,title='Granted release')
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='manage')
        ReleaseTrack.objects.create(release=release,recording=self.hidden,track_number=1,display_title='PRIVATE TITLE')
        asset=Asset.objects.create(organization=self.org,source_url='https://example.test/private.png')
        RecordingAsset.objects.create(recording=self.song,asset=asset,purpose='cover',is_primary=True)
        self.assertEqual(self.api.get(self.root+'/tracks/').json()['count'],0)
        self.assertEqual(self.api.get(self.root+'/recording-assets/').json()['count'],0)

    def test_deleted_primary_artwork_can_be_replaced(self):
        from catalog.models import RecordingAsset
        first=Asset.objects.create(organization=self.org,source_url='https://example.test/first.png')
        second=Asset.objects.create(organization=self.org,source_url='https://example.test/second.png')
        for asset in (first,second):
            ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='manage')
        row=RecordingAsset.objects.create(recording=self.song,asset=first,purpose='cover',is_primary=True)
        self.assertEqual(self.api.delete(self.root+f'/recording-assets/{row.pk}/',{'version':stamp(row)},format='json').status_code,204)
        result=self.api.post(self.root+'/recording-assets/',{'recording':str(self.song.pk),'asset':str(second.pk),'purpose':'cover','is_primary':True},format='json')
        self.assertEqual(result.status_code,201,result.content)
        self.assertEqual(self.api.get(self.root+'/recording-assets/').json()['results'][0]['asset'],str(second.pk))

    def test_read_only_record_detail_keeps_accessible_parent_label(self):
        row=RecordingIdentifier.objects.create(recording=self.song,scheme='isrc',value='READ-ONLY-ID')
        grant=ArtistResourceGrant.objects.get(artist=self.artist,recording=self.song)
        grant.capability='read';grant.save()
        self.membership.role='viewer';self.membership.save()
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'recording-identifiers',row.pk]))
        self.assertContains(response,'Visible Song')

    def test_private_asset_annotation_does_not_withdraw_rights_approval(self):
        asset=Asset.objects.create(organization=self.org,source_url='https://example.test/cover.png',visibility='public',rights_basis='Staff reviewed license',rights_approved=True)
        ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='manage')
        response=self.api.patch(self.root+f'/assets/{asset.pk}/',{'version':stamp(asset),'description':'Internal organization note'},format='json')
        self.assertEqual(response.status_code,200,response.content)
        asset.refresh_from_db();self.assertTrue(asset.rights_approved)
