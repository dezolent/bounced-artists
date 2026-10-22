from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from artists import models as a
from catalog import models as c
from events import models as e
from rights import models as r
from publishing import models as p
from portal.resources import RESOURCES


class ResourcePresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org=a.Organization.objects.get(slug='bounced-studio')
        cls.artist=a.Artist.objects.create(organization=cls.org,name='Stage Name',slug='presentation-stage',management_status='managed')
        cls.user=get_user_model().objects.create_user(username='presentation-owner')
        a.ArtistMembership.objects.create(user=cls.user,artist=cls.artist,role='owner')
        cls.recording=c.Recording.objects.create(organization=cls.org,title='Northern Lights')
        cls.release=c.Release.objects.create(organization=cls.org,title='Midnight EP')
        cls.work=r.MusicalWork.objects.create(organization=cls.org,title='Northern Composition')
        cls.party=r.Party.objects.create(organization=cls.org,party_type='person',display_name='Writer Name',legal_name='PRIVATE LEGAL SENTINEL')
        cls.asset=c.Asset.objects.create(organization=cls.org,display_name='Album cover',mime_type='image/png',storage_key='fixture/cover.png')
        cls.event=e.Event.objects.create(organization=cls.org,title='Night Show',local_date='2027-01-02')
        for name in ('recording','release','work','party','asset','event'):
            a.ArtistResourceGrant.objects.create(artist=cls.artist,capability='manage',**{name:getattr(cls,name)})
        cls.label=c.Label.objects.create(organization=cls.org,name='Indie Label')
        cls.group=c.ReleaseGroup.objects.create(organization=cls.org,title='Midnight Family')
        a.ArtistResourceGrant.objects.create(artist=cls.artist,label=cls.label,capability='manage')
        a.ArtistResourceGrant.objects.create(artist=cls.artist,release_group=cls.group,capability='manage')
        cls.genre=c.Genre.objects.create(name='Alternative Rock',slug='presentation-alt')
        cls.service=a.Service.objects.create(name='Example Music',slug='presentation-music')
        cls.platform=c.PlatformObject.objects.create(service=cls.service,object_type='track',title='Platform Song',url='https://example.test/listen')
        cls.society=r.RightsSociety.objects.create(name='Example Society',code='EXAMPLE',society_type='performance')
        cls.website=p.Website.objects.create(organization=cls.org,artist=cls.artist,domain='presentation.example.test')
        cls.page=p.Page.objects.create(website=cls.website,path='/about',page_type='about',title='About Stage')
        cls.fanlink=p.FanlinkPage.objects.create(website=cls.website,recording=cls.recording,slug='lights',title='Listen Here')
        cls.registration=r.WorkRegistration.objects.create(work=cls.work,society=cls.society,external_work_id='WORK-123')
        cls.fixtures={
            'labels':(cls.label,'Indie Label','Indie Label'),
            'release-groups':(cls.group,'Midnight Family','Midnight Family'),
            'recording-labels':(c.RecordingLabel.objects.create(recording=cls.recording,label=cls.label),'Northern Lights','Indie Label'),
            'release-labels':(c.ReleaseLabel.objects.create(release=cls.release,label=cls.label),'Midnight EP','Indie Label'),
            'release-genres':(c.ReleaseGenre.objects.create(release=cls.release,genre=cls.genre),'Midnight EP','Alternative Rock'),
            'release-group-genres':(c.ReleaseGroupGenre.objects.create(release_group=cls.group,genre=cls.genre),'Midnight Family','Alternative Rock'),
            'media':(c.Medium.objects.create(release=cls.release,position=1,title='First Disc'),'Midnight EP','First Disc'),
            'work-identifiers':(r.WorkIdentifier.objects.create(work=cls.work,scheme='iswc',value='T-123456789'),'Northern Composition','T-123456789'),
            'artists':(cls.artist,'Stage Name','Stage Name'),
            'recordings':(cls.recording,'Northern Lights','Northern Lights'),
            'releases':(cls.release,'Midnight EP','Midnight EP'),
            'assets':(cls.asset,'Album cover','Album cover'),
            'works':(cls.work,'Northern Composition','Northern Composition'),
            'parties':(cls.party,'Writer Name','Writer Name'),
            'party-identifiers':(r.PartyIdentifier.objects.create(party=cls.party,scheme='ipi',value='123456'),'Writer Name','Writer Name'),
            'affiliations':(r.PartyAffiliation.objects.create(party=cls.party,society=cls.society),'Writer Name','Example Society'),
            'artist-parties':(r.ArtistParty.objects.create(artist=cls.artist,party=cls.party,relationship_type='songwriter'),'Writer Name','songwriter'),
            'events':(cls.event,'Night Show','Night Show'),
            'aliases':(a.ArtistAlias.objects.create(artist=cls.artist,name='Another Name'),'Another Name','Stage Name'),
            'contacts':(a.ArtistContact.objects.create(artist=cls.artist,purpose='general',label='Booking desk',value='booking@example.test',visibility='members'),'Booking desk','booking@example.test'),
            'profile-links':(a.ArtistProfileLink.objects.create(artist=cls.artist,platform=cls.service,url='https://example.test/artist'),'Example Music','Stage Name'),
            'artist-genres':(c.ArtistGenre.objects.create(artist=cls.artist,genre=cls.genre),'Stage Name','Alternative Rock'),
            'artist-assets':(c.ArtistAsset.objects.create(artist=cls.artist,asset=cls.asset,purpose='portrait'),'Album cover','Stage Name'),
            'recording-identifiers':(c.RecordingIdentifier.objects.create(recording=cls.recording,scheme='isrc',value='USAAA2600001'),'Northern Lights','USAAA2600001'),
            'release-identifiers':(c.ReleaseIdentifier.objects.create(release=cls.release,scheme='upc',value='123456789012'),'Midnight EP','123456789012'),
            'recording-credits':(c.RecordingArtist.objects.create(recording=cls.recording,artist=cls.artist,role='featured'),'Northern Lights','Stage Name'),
            'release-credits':(c.ReleaseArtist.objects.create(release=cls.release,artist=cls.artist,role='primary'),'Midnight EP','Stage Name'),
            'recording-genres':(c.RecordingGenre.objects.create(recording=cls.recording,genre=cls.genre),'Northern Lights','Alternative Rock'),
            'recording-relations':(None,'Northern Lights','Evening Song'),
            'tracks':(c.ReleaseTrack.objects.create(release=cls.release,recording=cls.recording,track_number=2),'Northern Lights','Midnight EP'),
            'recording-assets':(c.RecordingAsset.objects.create(recording=cls.recording,asset=cls.asset,purpose='cover'),'Album cover','Northern Lights'),
            'release-assets':(c.ReleaseAsset.objects.create(release=cls.release,asset=cls.asset,purpose='cover'),'Album cover','Midnight EP'),
            'recording-links':(c.RecordingPlatformMatch.objects.create(recording=cls.recording,platform_object=cls.platform),'Northern Lights','Example Music'),
            'release-links':(c.ReleasePlatformMatch.objects.create(release=cls.release,platform_object=cls.platform),'Midnight EP','Example Music'),
            'fanlinks':(cls.fanlink,'Listen Here','Northern Lights'),
            'destinations':(p.FanlinkDestination.objects.create(fanlink_page=cls.fanlink,platform_object=cls.platform),'Listen Here','Example Music'),
            'pages':(cls.page,'About Stage','presentation.example.test'),
            'seo':(p.SEORecord.objects.create(page=cls.page,title='Discover Stage'),'About Stage','Discover Stage'),
            'work-aliases':(r.WorkAlias.objects.create(work=cls.work,title='Composition Alias'),'Northern Composition','Composition Alias'),
            'recording-works':(r.RecordingWork.objects.create(recording=cls.recording,work=cls.work),'Northern Lights','Northern Composition'),
            'contributors':(r.WorkContributor.objects.create(work=cls.work,party=cls.party,role='composer'),'Northern Composition','Writer Name'),
            'registrations':(cls.registration,'Northern Composition','Example Society'),
            'claims':(r.RightsClaim.objects.create(registration=cls.registration,party=cls.party,right_type='performance',share_value='50',share_basis='percent'),'Northern Composition','Writer Name'),
            'event-artists':(e.EventArtist.objects.create(event=cls.event,artist=cls.artist),'Night Show','Stage Name'),
            'tickets':(e.TicketOffer.objects.create(event=cls.event,url='https://example.test/tickets'),'Night Show','https://example.test/tickets'),
        }
        other=c.Recording.objects.create(organization=cls.org,title='Evening Song')
        a.ArtistResourceGrant.objects.create(artist=cls.artist,recording=other,capability='manage')
        relation=c.RecordingRelation.objects.create(from_recording=cls.recording,to_recording=other,relation_type='remix_of')
        cls.fixtures['recording-relations']=(relation,'Northern Lights','Evening Song')

    def setUp(self):
        self.client.force_login(self.user)

    def index(self,kind,query=''):
        return self.client.get(reverse('portal:resources',args=[self.artist.pk,kind]),{'q':query})

    def test_every_resource_index_names_its_parent_and_target(self):
        self.assertEqual(set(self.fixtures),set(RESOURCES))
        for kind,(_,parent,target) in self.fixtures.items():
            with self.subTest(kind=kind):
                response=self.index(kind)
                self.assertEqual(response.status_code,200)
                self.assertContains(response,parent)
                self.assertContains(response,target)
                visible=' '.join(f"{row['label']} {row.get('subtitle','')}" for row in response.context['rows'])
                self.assertIn(parent,visible)
                self.assertIn(target,visible)
                self.assertNotContains(response,' object (')
                self.assertNotContains(response,'PRIVATE LEGAL SENTINEL')

    def test_related_names_and_metadata_are_searchable(self):
        for kind,(_,parent,target) in self.fixtures.items():
            for query in {parent,target}:
                with self.subTest(kind=kind,query=query):
                    response=self.index(kind,query)
                    self.assertEqual(response.context['page_obj'].paginator.count,1)

    def test_related_search_keeps_artist_scope_and_removed_rows(self):
        hidden=c.Recording.objects.create(organization=self.org,title='Hidden Track')
        c.RecordingGenre.objects.create(recording=hidden,genre=self.genre)
        self.assertEqual(self.index('recording-genres','Alternative').context['page_obj'].paginator.count,1)
        from portal.models import EditorialChange
        row=self.fixtures['recording-genres'][0]
        EditorialChange.objects.create(organization=self.org,artist=self.artist,user=self.user,model_label=row._meta.label_lower,object_id=str(row.pk),action='delete')
        self.assertEqual(self.index('recording-genres','Alternative').context['page_obj'].paginator.count,0)

    def test_presenting_related_rows_does_not_add_per_row_queries(self):
        from portal.presentation import prepare_queryset,present
        for number in range(8):
            song=c.Recording.objects.create(organization=self.org,title=f'Song {number}')
            c.RecordingGenre.objects.create(recording=song,genre=self.genre)
        rows=prepare_queryset(c.RecordingGenre.objects.all(),'recording-genres')
        with self.assertNumQueries(1):
            rendered=[present(row,'recording-genres') for row in rows]
        self.assertTrue(all('Alternative Rock' in row['label'] for row in rendered))

    def test_role_scheme_track_position_and_claim_basis_are_explicit(self):
        for kind,expected in (
            ('artist-parties',('Role','songwriter')),
            ('recording-credits',('Role','featured','Billing order')),
            ('recording-identifiers',('Scheme','isrc','USAAA2600001')),
            ('tracks',('Disc','Track','2','Midnight EP')),
            ('claims',('Right','performance','Share','50','Basis','percent')),
            ('media',('Disc','1','First Disc')),
        ):
            with self.subTest(kind=kind):
                row=self.index(kind).context['rows'][0]
                visible=row['label']+' '+row['subtitle']
                for text in expected:
                    self.assertIn(text,visible)

    def test_legal_names_are_not_search_keys(self):
        for kind in ('parties','artist-parties','contributors','claims'):
            with self.subTest(kind=kind):
                self.assertEqual(self.index(kind,'PRIVATE LEGAL SENTINEL').context['page_obj'].paginator.count,0)

    def test_selectors_name_related_entities_and_keep_scope(self):
        from portal.forms import make_form
        expected={
            ('recording-genres','recording'):('Northern Lights',),
            ('artist-parties','party'):('Writer Name',),
            ('claims','registration'):('Northern Composition','Example Society','WORK-123'),
            ('destinations','platform_object'):('Example Music','Platform Song'),
            ('tracks','medium'):('Midnight EP','First Disc','Disc'),
            ('pages','website'):('presentation.example.test',),
        }
        for (kind,name),texts in expected.items():
            with self.subTest(kind=kind,field=name):
                field=make_form(kind,self.user,self.artist).fields[name]
                labels=' '.join(str(label) for _,label in field.choices)
                for text in texts:
                    self.assertIn(text,labels)
                self.assertNotIn(' object (',labels)
                self.assertNotIn('PRIVATE LEGAL SENTINEL',labels)
        hidden=c.Recording.objects.create(organization=self.org,title='Selector hidden song')
        field=make_form('recording-genres',self.user,self.artist).fields['recording']
        self.assertNotIn(hidden.pk,list(field.queryset.values_list('pk',flat=True)))

    def test_search_accepts_readable_relationship_words(self):
        self.assertEqual(self.index('recording-relations','remix of').context['page_obj'].paginator.count,1)
        self.assertEqual(self.index('recording-links','full recording').context['page_obj'].paginator.count,1)
