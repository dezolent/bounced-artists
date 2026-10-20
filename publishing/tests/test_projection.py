import importlib.util
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from artists.models import Artist, Organization, ArtistContact
from catalog.models import Recording
from publishing.models import Website, PublicationOutbox


class ProjectionAvailabilityTests(TestCase):
    def test_approved_projection_service_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('publishing.projection'))


class ProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Studio', slug='studio')
        cls.artist = Artist.objects.create(organization=cls.org, name='Artist', slug='artist', public_metadata={'secret': 'METADATA_SENTINEL'})
        cls.website = Website.objects.create(organization=cls.org, artist=cls.artist, domain='example.test')
        cls.staff = get_user_model().objects.create_user(username='reviewer', is_staff=True)
        cls.member = get_user_model().objects.create_user(username='member')
        ArtistContact.objects.create(artist=cls.artist, value='CONTACT_SENTINEL', visibility='public')

    def service(self):
        from publishing import projection
        return projection

    def preview(self, **selection):
        return self.service().preview_catalog(self.staff, self.website, selection=selection)

    def test_staff_review_is_required_and_does_not_deliver(self):
        service = self.service()
        with self.assertRaises(PermissionDenied):
            service.preview_catalog(self.member, self.website, selection={})
        preview = self.preview()
        with self.assertRaises(PermissionDenied):
            service.approve_catalog(self.member, self.website, preview)
        revision = service.approve_catalog(self.staff, self.website, preview)
        self.assertEqual(revision.content_version, 1)
        self.assertFalse(PublicationOutbox.objects.exists())
        self.assertEqual(set(revision.payload), {'artist', 'recordings', 'releases', 'fanlinks'})
        self.assertEqual(set(revision.payload['artist']), {'id','name','slug','bio_short','bio_long','location','artwork','profile_links'})
        self.assertNotIn('SENTINEL', str(revision.payload))
        self.assertNotEqual(revision.payload['artist']['id'], str(self.artist.pk))

    def test_stale_preview_and_unselected_or_unassigned_recordings_fail(self):
        from artists.models import ArtistResourceGrant
        service = self.service()
        recording = Recording.objects.create(organization=self.org, title='Draft')
        with self.assertRaises(ValidationError):
            self.preview(recordings=[str(recording.pk)])
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording, capability='read')
        with self.assertRaises(ValidationError):
            self.preview(recordings=[str(recording.pk)])
        recording.status = 'approved'
        recording.save()
        preview = self.preview(recordings=[str(recording.pk)])
        recording.title = 'Changed after preview'
        recording.save()
        with self.assertRaises(ValidationError):
            service.approve_catalog(self.staff, self.website, preview)

    @override_settings(ROOT_URLCONF='publishing.public_urls')
    def test_snapshot_privacy_immutability_revocation_and_write_denial(self):
        service = self.service()
        self.assertEqual(self.client.get('/artists/artist/').status_code, 404)
        revision = service.approve_catalog(self.staff, self.website, self.preview())
        response = self.client.get('/artists/artist/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'schema_version','content_version','artist'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn(b'SENTINEL', response.content)
        self.artist.name = 'Edited'; self.artist.save()
        self.assertEqual(self.client.get('/artists/artist/').content, response.content)
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(getattr(self.client, method)('/artists/artist/').status_code, 405)
        revision.payload['artist']['contacts'] = ['LEAK']
        with self.assertRaises(ValidationError): revision.save()
        service.revoke_catalog(self.staff, self.website)
        self.assertEqual(self.client.get('/artists/artist/').status_code, 404)

    def test_url_validation_rejects_private_hosts_and_credentials(self):
        validate = self.service().validate_public_url
        self.assertEqual(validate('spotify', 'https://open.spotify.com/track/abc'), 'https://open.spotify.com/track/abc')
        for url in ('http://open.spotify.com/track/abc','https://user@open.spotify.com/track/abc','https://localhost/a','https://open.spotify.com.evil.test/a','https://open.spotify.com/track/a?token=SECRET','https://open.spotify.com:444/track/a','https://open.spotify.com/track/a#token'):
            with self.subTest(url=url), self.assertRaises(ValidationError): validate('spotify', url)

    def test_payload_rejects_nested_json_and_unknown_fields(self):
        import copy
        service = self.service()
        payload = self.preview().payload
        payload['artist']['name'] = {'legal_name':'NESTED_SENTINEL'}
        with self.assertRaises(ValidationError): service.validate_payload(payload)
        payload = copy.deepcopy(self.preview().payload)
        payload['artist']['contacts'] = []
        with self.assertRaises(ValidationError): service.validate_payload(payload)

    @override_settings(ROOT_URLCONF='publishing.public_urls')
    def test_approved_catalog_nested_schema_and_pending_links(self):
        from datetime import date
        from artists.models import ArtistResourceGrant, Service
        from catalog.models import RecordingArtist, RecordingIdentifier, Genre, RecordingGenre, Release, ReleaseTrack, ReleaseArtist, PlatformObject, RecordingPlatformMatch
        from publishing.models import FanlinkPage, FanlinkDestination
        service = self.service()
        recording = Recording.objects.create(organization=self.org,title='<b>Gone</b>', status='approved', original_release_date=date(2024,1,1),date_precision='year')
        second = Recording.objects.create(organization=self.org,title='Chasing Ghosts',status='approved')
        release = Release.objects.create(organization=self.org,title='Release',status='approved')
        for resource,field in ((recording,'recording'),(second,'recording'),(release,'release')):
            ArtistResourceGrant.objects.create(artist=self.artist,capability='read',**{field:resource})
        for row in (recording,second):
            RecordingArtist.objects.create(recording=row,artist=self.artist)
            RecordingIdentifier.objects.create(recording=row,scheme='ISRC',value='IDENTIFIER_SENTINEL')
        ReleaseArtist.objects.create(release=release,artist=self.artist)
        ReleaseTrack.objects.create(release=release,recording=recording,track_number=1)
        genre=Genre.objects.create(name='Electronic',slug='electronic'); RecordingGenre.objects.create(recording=recording,genre=genre)
        platform_service=Service.objects.create(name='Spotify',slug='spotify')
        accepted=PlatformObject.objects.create(service=platform_service,object_type='track',url='https://open.spotify.com/track/abc')
        pending=PlatformObject.objects.create(service=platform_service,object_type='track',url='https://open.spotify.com/track/PENDING_SENTINEL')
        bad=PlatformObject.objects.create(service=platform_service,object_type='track',url='https://open.spotify.com/track/abc?token=TOKEN_SENTINEL')
        for obj,status in ((accepted,'accepted'),(pending,'pending'),(bad,'accepted')):
            RecordingPlatformMatch.objects.create(recording=recording,platform_object=obj,review_status=status)
        fanlink=FanlinkPage.objects.create(website=self.website,recording=recording,title='Listen',slug='listen',status='approved')
        FanlinkDestination.objects.create(fanlink_page=fanlink,platform_object=accepted)
        selection={'recordings':[str(recording.pk),str(second.pk)],'releases':[str(release.pk)],'fanlinks':[str(fanlink.pk)]}
        preview=self.preview(**selection)
        revision=service.approve_catalog(self.staff,self.website,preview)
        result=self.client.get('/artists/artist/recordings/').json()
        self.assertEqual(set(result),{'schema_version','content_version','artist','results','next'})
        self.assertEqual(len(result['results']),2)
        gone=next(row for row in result['results'] if row['title']=='Gone')
        self.assertEqual(set(gone),service.KEYS['recording'])
        self.assertEqual(gone['original_release_date'],'2024')
        self.assertNotEqual(gone['id'],str(recording.pk))
        self.assertNotEqual(gone['id'],next(row['id'] for row in result['results'] if row['title']=='Chasing Ghosts'))
        self.assertEqual(set(gone['artists'][0]),service.KEYS['credit'])
        self.assertEqual(set(gone['genres'][0]),service.KEYS['genre'])
        self.assertEqual(len(gone['platform_links']),1)
        self.assertEqual(set(gone['platform_links'][0]),service.KEYS['destination'])
        releases=self.client.get('/artists/artist/releases/').json()['results']
        self.assertEqual(set(releases[0]),service.KEYS['release'])
        self.assertEqual(set(releases[0]['tracks'][0]),service.KEYS['track'])
        fanlinks=self.client.get('/artists/artist/fanlinks/').json()['results']
        self.assertEqual(set(fanlinks[0]),service.KEYS['fanlink'])
        response=self.client.get('/artists/artist/recordings/')
        self.assertNotIn(b'SENTINEL',response.content)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get('/artists/artist/recordings/').content,response.content)
        self.assertEqual(self.client.get(f'/artists/artist/recordings/{recording.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/artists/artist/recordings/{gone["id"]}/').status_code,200)
        self.assertEqual(self.client.head('/artists/artist/').status_code,200)
        self.assertEqual(self.client.get('/artists/artist/recordings/?page_size=1').json()['next'],'/api/public/v1/artists/artist/recordings/?page=2&page_size=1')
        recording.title='New title'; recording.save()
        newer=service.approve_catalog(self.staff,self.website,self.preview(**selection))
        self.assertEqual(newer.content_version,revision.content_version+1)
        self.assertEqual(next(row['id'] for row in newer.payload['recordings'] if row['title']=='New title'),gone['id'])
        recording.archived_at=__import__('django.utils.timezone',fromlist=['now']).now(); recording.save()
        self.assertEqual(self.client.get('/artists/artist/').status_code,404)

    def test_cross_org_selection_and_omitted_release_tracks_are_rejected(self):
        from artists.models import ArtistResourceGrant
        from catalog.models import Release, ReleaseTrack
        other=Organization.objects.create(name='Other',slug='other')
        foreign=Recording.objects.create(organization=other,title='Foreign',status='approved')
        with self.assertRaises(ValidationError): self.preview(recordings=[str(foreign.pk)])
        recording=Recording.objects.create(organization=self.org,title='Track',status='approved')
        release=Release.objects.create(organization=self.org,title='Release',status='approved')
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='read')
        ReleaseTrack.objects.create(release=release,recording=recording,track_number=1)
        with self.assertRaises(ValidationError): self.preview(releases=[str(release.pk)])

    def test_revision_cannot_be_updated_through_queryset(self):
        from publishing.models import ApprovedCatalogRevision
        revision=self.service().approve_catalog(self.staff,self.website,self.preview())
        with self.assertRaises(ValidationError):
            ApprovedCatalogRevision.objects.filter(pk=revision.pk).update(payload=self.preview().payload)

    @override_settings(ROOT_URLCONF='publishing.public_urls')
    def test_artwork_needs_rights_selection_grant_and_sanitized_version(self):
        import tempfile
        from django.core.files.base import ContentFile
        from artists.models import ArtistResourceGrant
        from catalog.models import Asset, AssetVersion, ArtistAsset
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset=Asset.objects.create(organization=self.org,storage_key='STORAGE_SENTINEL',visibility='public',rights_basis='Owner',rights_approved=False,alt_text='Cover')
            version=AssetVersion.objects.create(asset=asset,file=ContentFile(b'PRIVATE_EXIF_SENTINEL',name='original.jpg'),original_name='PRIVATE_FILENAME_SENTINEL',byte_size=21,mime_type='image/jpeg',sha256='a'*64,width=1,height=1,creator=self.staff)
            asset.current_version=version; asset.save()
            ArtistAsset.objects.create(artist=self.artist,asset=asset,purpose='portrait')
            ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='read')
            self.assertIsNone(self.preview().payload['artist']['artwork'])
            with self.assertRaises(ValidationError): self.preview(artwork=[str(asset.pk)])
            asset.rights_approved=True; asset.save()
            with self.assertRaises(ValidationError): self.preview(artwork=[str(asset.pk)])
            safe_version=AssetVersion.objects.create(asset=asset,file=ContentFile(b'PRIVATE_EXIF_SENTINEL',name='original2.jpg'),preview_file=ContentFile(b'SAFE_RASTER',name='safe.webp'),preview_width=1,preview_height=1,original_name='PRIVATE_FILENAME_SENTINEL',byte_size=21,mime_type='image/jpeg',sha256='b'*64,width=1,height=1,creator=self.staff)
            asset.current_version=safe_version; asset.save()
            preview=self.preview(artwork=[str(asset.pk)])
            self.assertEqual(set(preview.payload['artist']['artwork']),self.service().KEYS['artwork'])
            self.service().approve_catalog(self.staff,self.website,preview)
            url=preview.payload['artist']['artwork']['url'].removeprefix('/api/public/v1')
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(b''.join(response.streaming_content),b'SAFE_RASTER')
            self.assertNotIn('SENTINEL',str(preview.payload))
            asset.visibility='private'; asset.save()
            self.assertEqual(self.client.get(url).status_code,404)
            self.assertEqual(self.client.get('/artists/artist/').status_code,404)

    @override_settings(ROOT_URLCONF='publishing.public_urls')
    def test_revoked_grant_withdraws_public_access(self):
        from artists.models import ArtistResourceGrant
        recording=Recording.objects.create(organization=self.org,title='Song',status='approved')
        grant=ArtistResourceGrant.objects.create(artist=self.artist,recording=recording,capability='read')
        self.service().approve_catalog(self.staff,self.website,self.preview(recordings=[str(recording.pk)]))
        self.assertEqual(self.client.get('/artists/artist/recordings/').status_code,200)
        grant.delete()
        self.assertEqual(self.client.get('/artists/artist/recordings/').status_code,404)

    def test_tombstoned_child_link_does_not_return_on_reapproval(self):
        from artists.models import Service, ArtistResourceGrant
        from catalog.models import PlatformObject, RecordingPlatformMatch
        from portal.models import EditorialChange
        recording=Recording.objects.create(organization=self.org,title='Song',status='approved')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=recording,capability='read')
        spotify=Service.objects.create(name='Spotify',slug='spotify')
        platform=PlatformObject.objects.create(service=spotify,object_type='track',url='https://open.spotify.com/track/abc')
        match=RecordingPlatformMatch.objects.create(recording=recording,platform_object=platform,review_status='accepted')
        self.assertEqual(len(self.preview(recordings=[str(recording.pk)]).payload['recordings'][0]['platform_links']),1)
        EditorialChange.objects.create(organization=self.org,artist=self.artist,user=self.staff,model_label=match._meta.label_lower,object_id=str(match.pk),action='delete')
        self.assertEqual(self.preview(recordings=[str(recording.pk)]).payload['recordings'][0]['platform_links'],[])

    @override_settings(ROOT_URLCONF='publishing.public_urls')
    def test_corrupted_snapshot_fails_closed_without_private_error_content(self):
        from publishing.models import ApprovedCatalogRevision
        revision=self.service().approve_catalog(self.staff,self.website,self.preview())
        ApprovedCatalogRevision._base_manager.filter(pk=revision.pk).update(selection={'recordings':['not-a-uuid-SENTINEL']})
        response=self.client.get('/artists/artist/')
        self.assertEqual(response.status_code,404)
        self.assertNotIn(b'SENTINEL',response.content)
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_unselected_artwork_cannot_be_attached_to_revision(self):
        import tempfile
        from uuid import uuid4
        from django.core.files.base import ContentFile
        from catalog.models import Asset, AssetVersion
        from publishing.models import ApprovedArtwork
        revision=self.service().approve_catalog(self.staff,self.website,self.preview())
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset=Asset.objects.create(organization=self.org,storage_key='internal',visibility='public',rights_basis='Owner',rights_approved=True)
            version=AssetVersion.objects.create(asset=asset,file=ContentFile(b'original',name='x.jpg'),preview_file=ContentFile(b'safe',name='preview.webp'),preview_width=1,preview_height=1,original_name='private.jpg',byte_size=8,mime_type='image/jpeg',sha256='a'*64,width=1,height=1,creator=self.staff)
            with self.assertRaises(ValidationError): ApprovedArtwork.objects.create(revision=revision,version=version,public_id=uuid4())

    def test_approval_and_revocation_record_bounded_private_audit(self):
        from provenance.models import AuditEvent
        self.service().approve_catalog(self.staff,self.website,self.preview())
        self.service().revoke_catalog(self.staff,self.website)
        self.assertEqual(set(AuditEvent.objects.values_list('action',flat=True)),{'public_catalog.approve','public_catalog.revoke'})
        for event in AuditEvent.objects.all():
            self.assertEqual(event.user,self.staff)
            self.assertEqual(event.organization,self.org)
            self.assertEqual(set(event.change_json),{'website_id','content_version'})

    def test_preview_reads_current_rows_when_caller_retains_stale_instances(self):
        Artist.objects.filter(pk=self.artist.pk).update(name='Current artist name')
        self.assertEqual(self.preview().payload['artist']['name'],'Current artist name')

    @override_settings(ROOT_URLCONF='publishing.public_urls',PORTAL_UPLOADS_ENABLED=True)
    def test_public_artwork_metadata_describes_sanitized_derivative(self):
        import io
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from artists.models import ArtistResourceGrant
        from catalog.models import Asset, ArtistAsset
        from portal.media import save_upload
        image=Image.new('RGB',(3000,1000),'blue')
        original=io.BytesIO(); image.save(original,format='PNG')
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            asset=Asset.objects.create(organization=self.org,storage_key='pending',visibility='public',rights_basis='Owner')
            ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='read')
            version=save_upload(asset,SimpleUploadedFile('cover.png',original.getvalue()),self.staff,self.artist)
            asset.rights_approved=True; asset.save()
            ArtistAsset.objects.create(artist=self.artist,asset=asset,purpose='portrait')
            preview=self.preview(artwork=[str(asset.pk)])
            artwork=preview.payload['artist']['artwork']
            self.assertEqual(artwork['mime_type'],'image/webp')
            self.assertEqual((artwork['width'],artwork['height']),(2400,800))
            self.service().approve_catalog(self.staff,self.website,preview)
            response=self.client.get(artwork['url'].removeprefix('/api/public/v1'))
            self.assertEqual(response['Content-Type'],'image/webp')
            self.assertEqual(response['Content-Disposition'],'inline; filename="artwork.webp"')
            with Image.open(io.BytesIO(b''.join(response.streaming_content))) as rendered:
                self.assertEqual(rendered.format,'WEBP')
                self.assertEqual(rendered.size,(artwork['width'],artwork['height']))
            self.assertEqual(version.mime_type,'image/png')
