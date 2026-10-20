from urllib.parse import parse_qs,urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from artists.models import Artist,ArtistMembership,ArtistResourceGrant,Organization,Service
from catalog.models import (
    Asset,Genre,Recording,RecordingArtist,RecordingAsset,RecordingGenre,
    RecordingIdentifier,RecordingPlatformMatch,PlatformObject,Release,ReleaseArtist,
    ReleaseAsset,ReleaseIdentifier,ReleasePlatformMatch,ReleaseTrack,
)
from rights.models import MusicalWork,Party,RecordingWork
from portal.forms import stamp
from portal.querysets import scoped,choices
from portal.access import artists_for


class WorkspaceSelectionTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.dezolent=Artist.objects.create(organization=self.org,name='Dezolent',slug='dezolent',bootstrap_key='dezolent',management_status='managed')
        self.sry4u=Artist.objects.create(organization=self.org,name='SRY4U',slug='sry4u',bootstrap_key='sry4u',management_status='managed')
        self.morbeato=Artist.objects.create(organization=self.org,name='Morbeato',slug='morbeato',bootstrap_key='morbeato',management_status='managed')
        self.collaborator=Artist.objects.create(organization=self.org,name='Bad Color',slug='bad-color')
        self.staff=get_user_model().objects.create_user(username='staff',is_staff=True)
        self.client.force_login(self.staff)
        self.api=APIClient();self.api.force_authenticate(self.staff)
        self.song=Recording.objects.create(organization=self.org,title='Dezolent recording')
        self.release=Release.objects.create(organization=self.org,title='Dezolent release')
        self.asset=Asset.objects.create(organization=self.org,source_url='https://example.test/dezolent.webp')
        self.work=MusicalWork.objects.create(organization=self.org,title='Dezolent work')
        self.party=Party.objects.create(organization=self.org,party_type='person',display_name='Dezolent party')
        for kind,obj in [('recording',self.song),('release',self.release),('asset',self.asset),('work',self.work),('party',self.party)]:
            ArtistResourceGrant.objects.create(artist=self.dezolent,capability='manage',**{kind:obj})
        RecordingArtist.objects.create(recording=self.song,artist=self.dezolent)
        RecordingArtist.objects.create(recording=self.song,artist=self.collaborator,role='featured',billing_order=2)
        ReleaseArtist.objects.create(release=self.release,artist=self.dezolent)
        RecordingIdentifier.objects.create(recording=self.song,scheme='isrc',value='OWNED-ISRC')
        ReleaseIdentifier.objects.create(release=self.release,scheme='upc',value='OWNED-UPC')
        ReleaseTrack.objects.create(release=self.release,recording=self.song,track_number=1)
        genre=Genre.objects.create(name='Test genre',slug='test-genre')
        RecordingGenre.objects.create(recording=self.song,genre=genre)
        RecordingAsset.objects.create(recording=self.song,asset=self.asset,purpose='cover',is_primary=True)
        ReleaseAsset.objects.create(release=self.release,asset=self.asset,purpose='cover',is_primary=True)
        service=Service.objects.create(name='Spotify',slug='spotify')
        listing=PlatformObject.objects.create(service=service,object_type='track',url='https://open.spotify.com/track/EXAMPLE')
        RecordingPlatformMatch.objects.create(recording=self.song,platform_object=listing)
        ReleasePlatformMatch.objects.create(release=self.release,platform_object=listing)
        RecordingWork.objects.create(recording=self.song,work=self.work)

    def test_only_managed_artists_are_workspace_choices_even_for_staff(self):
        response=self.client.get('/app/')
        self.assertEqual({row.pk for row in response.context['artists']},{self.dezolent.pk,self.sry4u.pk,self.morbeato.pk})
        self.assertEqual(response.context['artist'],self.dezolent)
        self.assertEqual(self.client.get(reverse('portal:catalog',args=[self.collaborator.pk])).status_code,404)

    def test_staff_selected_workspace_requires_explicit_grants_for_all_catalog_families(self):
        kinds=('recordings','releases','assets','works','parties','recording-identifiers','release-identifiers','tracks','recording-credits','release-credits','recording-genres','recording-assets','release-assets','recording-links','release-links','recording-works')
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertGreater(scoped(self.staff,self.dezolent,kind).count(),0)
                self.assertEqual(scoped(self.staff,self.sry4u,kind).count(),0)
                response=self.api.get(f'/api/portal/v1/artists/{self.sry4u.pk}/{kind}/')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['count'],0)
        for kind,obj in [('recordings',self.song),('releases',self.release),('assets',self.asset),('works',self.work),('parties',self.party)]:
            with self.subTest(detail=kind):
                url=f'/api/portal/v1/artists/{self.sry4u.pk}/{kind}/{obj.pk}/'
                self.assertEqual(self.api.get(url).status_code,404)
                self.assertEqual(self.api.patch(url,{'version':stamp(obj)},format='json').status_code,404)

    def test_staff_relationship_choices_do_not_offer_another_workspace_records(self):
        for model in (Recording,Release,Asset,MusicalWork,Party):
            with self.subTest(model=model._meta.label):
                self.assertEqual(choices(self.staff,self.sry4u,model).count(),0)
        # Collaborators remain usable as credits; they do not become workspaces.
        self.assertIn(self.collaborator,choices(self.staff,self.dezolent,Artist))

    def test_staff_cannot_read_change_or_attach_another_workspace_child_rows(self):
        identifier=self.song.identifiers.get()
        endpoint=f'/api/portal/v1/artists/{self.sry4u.pk}/recording-identifiers/'
        detail=f'{endpoint}{identifier.pk}/'
        self.assertEqual(self.api.get(detail).status_code,404)
        self.assertEqual(self.api.patch(detail,{'value':'CHANGED','version':stamp(identifier)},format='json').status_code,404)
        self.assertEqual(self.api.delete(detail,{'version':stamp(identifier)},format='json').status_code,404)
        self.assertEqual(self.api.post(endpoint,{'recording':str(self.song.pk),'scheme':'isrc','value':'ATTACHED'},format='json').status_code,400)
        identifier.refresh_from_db()
        self.assertEqual(identifier.value,'OWNED-ISRC')
        self.assertEqual(self.song.identifiers.count(),1)
        for name,kind,obj in [('recording',None,self.song),('release',None,self.release),('detail','assets',self.asset)]:
            args=[self.sry4u.pk]+([kind] if kind else [])+[obj.pk]
            self.assertEqual(self.client.get(reverse(f'portal:{name}',args=args)).status_code,404)

    def test_explicitly_shared_record_keeps_real_credits_in_each_workspace(self):
        ArtistResourceGrant.objects.create(artist=self.sry4u,recording=self.song,capability='read')
        response=self.client.get(reverse('portal:catalog',args=[self.sry4u.pk]))
        card=response.context['recordings'][0]
        self.assertEqual(card.artist_display,'Dezolent · Bad Color')
        self.assertNotContains(response,'<p class="card-artist">SRY4U',html=False)
        detail=self.client.get(reverse('portal:recording',args=[self.sry4u.pk,self.song.pk]))
        self.assertEqual(detail.context['recording'].artist_display,'Dezolent · Bad Color')

    def test_selection_survives_overview_navigation_and_refresh(self):
        self.client.get(reverse('portal:catalog',args=[self.sry4u.pk]))
        for _ in range(2):
            response=self.client.get('/app/')
            self.assertEqual(response.context['artist'],self.sry4u)
            self.assertEqual(response.context['counts']['recordings'],0)
        self.client.get(reverse('portal:resources',args=[self.morbeato.pk,'works']))
        self.assertEqual(self.client.get('/app/').context['artist'],self.morbeato)

    def test_explicit_overview_selection_is_tab_safe_and_invalid_selections_fail_closed(self):
        response=self.client.get(reverse('portal:catalog',args=[self.dezolent.pk]))
        dezolent_overview=response.context['overview_url']
        self.client.get(reverse('portal:catalog',args=[self.sry4u.pk]))
        self.assertEqual(self.client.get(dezolent_overview).context['artist'],self.dezolent)
        self.assertEqual(self.client.get('/app/?artist=invalid').status_code,404)
        self.assertEqual(self.client.get(f'/app/?artist={self.collaborator.pk}').status_code,404)
        session=self.client.session
        session['portal_artist_id']='invalid'
        session.save()
        self.assertEqual(self.client.get('/app/').context['artist'],self.dezolent)

    def test_workspace_switch_links_preserve_section_and_safe_filters(self):
        cases=[
            ('/app/',reverse('portal:overview'),{}),
            (reverse('portal:catalog',args=[self.dezolent.pk])+'?kind=releases&q=Demo&status=draft&page=5',reverse('portal:catalog',args=[self.sry4u.pk]),{'kind':['releases'],'q':['Demo'],'status':['draft']}),
            (reverse('portal:resources',args=[self.dezolent.pk,'works'])+'?q=Demo&page=3',reverse('portal:resources',args=[self.sry4u.pk,'works']),{'q':['Demo']}),
            (reverse('portal:artist',args=[self.dezolent.pk]),reverse('portal:artist',args=[self.sry4u.pk]),{}),
        ]
        for source,path,filters in cases:
            with self.subTest(source=source):
                response=self.client.get(source)
                target=next(row for row in response.context['artists'] if row.pk==self.sry4u.pk)
                parts=urlsplit(target.workspace_url)
                self.assertEqual(parts.path,path)
                params=parse_qs(parts.query)
                if path=='/app/':
                    self.assertEqual(params.pop('artist'),[str(self.sry4u.pk)])
                self.assertEqual(params,filters)
                self.assertContains(response,f'href="{target.workspace_url.replace("&","&amp;")}"')
                selected=self.client.get(target.workspace_url)
                self.assertEqual(selected.status_code,200)
                self.assertEqual(selected.context['artist'],self.sry4u)

    def test_switching_from_a_song_detail_does_not_reuse_its_id_in_other_workspace(self):
        response=self.client.get(reverse('portal:recording',args=[self.dezolent.pk,self.song.pk]))
        target=next(row for row in response.context['artists'] if row.pk==self.sry4u.pk)
        self.assertEqual(target.workspace_url,reverse('portal:catalog',args=[self.sry4u.pk]))

    def test_removed_membership_or_management_state_cannot_reuse_saved_selection(self):
        member=get_user_model().objects.create_user(username='member')
        ArtistMembership.objects.create(user=member,artist=self.dezolent,role='viewer')
        other=ArtistMembership.objects.create(user=member,artist=self.sry4u,role='viewer')
        self.client.force_login(member)
        self.client.get(reverse('portal:catalog',args=[self.sry4u.pk]))
        other.delete()
        self.assertEqual(self.client.get('/app/').context['artist'],self.dezolent)
        self.dezolent.management_status='unmanaged';self.dezolent.save()
        self.assertFalse(artists_for(member).exists())
        self.assertEqual(self.client.get('/app/').status_code,200)
