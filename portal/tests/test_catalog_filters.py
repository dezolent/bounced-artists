from datetime import date
from urllib.parse import parse_qs,urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from artists.models import Artist,ArtistMembership,ArtistResourceGrant,Organization
from catalog.models import Recording,RecordingArtist,RecordingGenre,Genre,Release,ReleaseGenre,ReleaseTrack
from portal.models import EditorialChange


class CatalogFiltersTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Filter Artist',slug='filter-artist',management_status='managed')
        self.other=Artist.objects.create(organization=self.org,name='Other workspace',slug='filter-other',management_status='managed')
        self.guest=Artist.objects.create(organization=self.org,name='Guest',slug='filter-guest')
        self.user=get_user_model().objects.create_user(username='filters')
        for artist in (self.artist,self.other): ArtistMembership.objects.create(artist=artist,user=self.user,role='owner')
        self.client.force_login(self.user)
        self.url=reverse('portal:catalog',args=[self.artist.pk])
        self.genre=Genre.objects.create(name='Test Electronic',slug='test-electronic')
        self.solo=self.song('Zulu solo',date(2020,1,1))
        self.collab=self.song('Alpha collab',date(2026,1,1))
        RecordingArtist.objects.create(recording=self.collab,artist=self.guest,role='featured')
        self.remix=self.song('Beta remix',date(2025,1,1),recording_type='remix')
        RecordingArtist.objects.create(recording=self.remix,artist=self.guest,role='collaborator')
        self.unknown=self.song('A date unknown',None)
        RecordingGenre.objects.create(recording=self.collab,genre=self.genre)
        RecordingGenre.objects.create(recording=self.remix,genre=self.genre)

    def song(self,title,released,**kwargs):
        song=Recording.objects.create(organization=self.org,title=title,original_release_date=released,**kwargs)
        ArtistResourceGrant.objects.create(artist=self.artist,recording=song,capability='manage')
        RecordingArtist.objects.create(recording=song,artist=self.artist)
        return song

    def titles(self,**params):
        return [row.title for row in self.client.get(self.url,params).context['recordings']]

    def test_songs_sort_by_release_date_with_unknown_dates_last(self):
        self.assertEqual(self.titles(),['Alpha collab','Beta remix','Zulu solo','A date unknown'])

    def test_releases_sort_by_release_date_with_unknown_dates_last(self):
        for title,released in [('A old',date(2020,1,1)),('B unknown',None),('Z newest',date(2026,1,1))]:
            obj=Release.objects.create(organization=self.org,title=title,release_date=released)
            ArtistResourceGrant.objects.create(artist=self.artist,release=obj,capability='manage')
        self.assertEqual(self.titles(kind='releases'),['Z newest','A old','B unknown'])

    def test_song_type_filters_are_exclusive_and_ignore_nonperforming_credits(self):
        RecordingArtist.objects.create(recording=self.solo,artist=self.guest,role='producer')
        self.assertEqual(self.titles(song_type='solo'),['Zulu solo','A date unknown'])
        self.assertEqual(self.titles(song_type='collaborations'),['Alpha collab'])
        self.assertEqual(self.titles(song_type='remixes'),['Beta remix'])

    def test_filters_combine_search_genre_type_and_status(self):
        self.assertEqual(self.titles(q='Alpha',genre=self.genre.slug,song_type='collaborations',status='draft'),['Alpha collab'])
        self.assertEqual(self.titles(genre=self.genre.slug,song_type='solo'),[])

    def test_removed_credits_and_genres_do_not_affect_filters(self):
        for obj in (RecordingArtist.objects.get(recording=self.collab,artist=self.guest),RecordingGenre.objects.get(recording=self.collab)):
            EditorialChange.objects.create(organization=self.org,artist=self.artist,user=self.user,model_label=obj._meta.label_lower,object_id=str(obj.pk),action='delete')
        self.assertEqual(self.titles(song_type='collaborations'),[])
        self.assertEqual(self.titles(genre=self.genre.slug),['Beta remix'])

    def test_release_genre_filter_includes_assigned_active_track_genres(self):
        release=Release.objects.create(organization=self.org,title='Track genre release')
        ArtistResourceGrant.objects.create(artist=self.artist,release=release,capability='manage')
        track=ReleaseTrack.objects.create(release=release,recording=self.collab,track_number=1)
        self.assertEqual(self.titles(kind='releases',genre=self.genre.slug),['Track genre release'])
        track.archived_at=timezone.now();track.save()
        self.assertEqual(self.titles(kind='releases',genre=self.genre.slug),[])
        ReleaseGenre.objects.create(release=release,genre=self.genre)
        self.assertEqual(self.titles(kind='releases',genre=self.genre.slug),['Track genre release'])

    def test_genre_options_and_results_never_include_other_workspace_records(self):
        hidden=self.song('Unassigned remix',date(2026,10,1),recording_type='remix')
        ArtistResourceGrant.objects.filter(artist=self.artist,recording=hidden).delete()
        hidden_genre=Genre.objects.create(name='Hidden genre',slug='hidden-filter-genre')
        RecordingGenre.objects.create(recording=hidden,genre=hidden_genre)
        response=self.client.get(self.url)
        self.assertNotContains(response,'Hidden genre')
        self.assertNotContains(response,'Unassigned remix')
        self.assertEqual(self.titles(genre=hidden_genre.slug),[])

    def test_pagination_and_workspace_switch_preserve_filters_but_reset_page(self):
        for index in range(25):
            song=self.song(f'Match {index:02}',date(2026,1,1),recording_type='remix')
            RecordingGenre.objects.create(recording=song,genre=self.genre)
        response=self.client.get(self.url,{'q':'Match','genre':self.genre.slug,'song_type':'remixes','status':'draft','page':'2'})
        self.assertEqual(len(response.context['recordings']),1)
        self.assertContains(response,'song_type=remixes')
        other=next(row for row in response.context['artists'] if row.pk==self.other.pk)
        params=parse_qs(urlsplit(other.workspace_url).query)
        self.assertEqual(params,{'q':['Match'],'genre':[self.genre.slug],'song_type':['remixes'],'status':['draft']})
