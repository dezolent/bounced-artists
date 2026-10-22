"""End-to-end catalog editing contracts, including scope and rollback."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization, Service
from catalog.models import Recording, RecordingArtist, RecordingIdentifier, RecordingGenre, Genre


class CatalogEditorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Editor Artist', slug='editor-artist', management_status='managed')
        self.user = get_user_model().objects.create_user(username='catalog-editor', password='test-password')
        ArtistMembership.objects.create(artist=self.artist, user=self.user, role='owner')
        self.song = Recording.objects.create(organization=self.org, title='Before')
        ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song, capability='manage')
        self.client.force_login(self.user)
        self.edit_url = reverse('portal:edit', args=[self.artist.pk, 'recordings', self.song.pk])

    def payload(self, url=None):
        response = self.client.get(url or self.edit_url)
        editor = response.context['editor']
        data = {}
        for field in editor.form:
            value = field.value()
            if value is not None:
                data[field.html_name] = value
        for section in editor.sections:
            for field in section.formset.management_form:
                data[field.html_name] = field.value()
            # These tests start without child rows.
            data[f'{section.prefix}-TOTAL_FORMS'] = '0'
        return data

    def test_details_are_one_page_with_all_sections(self):
        response = self.client.get(reverse('portal:recording', args=[self.artist.pk, self.song.pk]))
        self.assertNotContains(response, 'data-tab-panel')
        for heading in ('Recording details', 'Appears on', 'Related works', 'Listen on', 'Fanlinks', 'Lyrics & files'):
            self.assertContains(response, heading)

    def test_one_save_updates_song_and_related_catalog_rows(self):
        collaborator = Artist.objects.create(organization=self.org, name='Guest', slug='guest')
        genre = Genre.objects.create(name='Electronic test', slug='electronic-test')
        service = Service.objects.create(name='Example Music', slug='example-music')
        data = self.payload()
        data.update({'title': 'After', 'credits-TOTAL_FORMS': '1', 'credits-0-artist': str(collaborator.pk), 'credits-0-role': 'featured', 'credits-0-billing_order': '1',
                     'identifiers-TOTAL_FORMS': '1', 'identifiers-0-scheme': 'isrc', 'identifiers-0-value': 'USAAA2600001',
                     'genres-TOTAL_FORMS': '1', 'genres-0-genre': str(genre.pk),
                     'links-TOTAL_FORMS': '1', 'links-0-service': str(service.pk), 'links-0-url': 'https://music.example.test/song', 'links-0-relationship_type': 'full_recording'})
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'After')
        self.assertTrue(RecordingArtist.objects.filter(recording=self.song, artist=collaborator, role='featured').exists())
        self.assertTrue(RecordingIdentifier.objects.filter(recording=self.song, value='USAAA2600001').exists())
        self.assertTrue(RecordingGenre.objects.filter(recording=self.song, genre=genre).exists())
        self.assertEqual(self.song.recordingplatformmatch_set.get().platform_object.url, 'https://music.example.test/song')

    def test_invalid_child_does_not_save_parent(self):
        data = self.payload()
        data.update({'title': 'Must roll back', 'identifiers-TOTAL_FORMS': '1', 'identifiers-0-scheme': 'isrc', 'identifiers-0-value': ''})
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 400)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'Before')
        self.assertContains(response, 'Must roll back', status_code=400)

    def test_foreign_child_id_cannot_be_attached_or_modified(self):
        other = Recording.objects.create(organization=self.org, title='Hidden')
        identifier = RecordingIdentifier.objects.create(recording=other, scheme='isrc', value='KEEP')
        data = self.payload()
        data.update({'identifiers-TOTAL_FORMS': '1', 'identifiers-0-row_id': str(identifier.pk), 'identifiers-0-scheme': 'isrc', 'identifiers-0-value': 'STOLEN'})
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 400)
        identifier.refresh_from_db()
        self.assertEqual(identifier.value, 'KEEP')

    def test_child_edit_after_opening_form_rejects_stale_graph(self):
        data = self.payload()
        RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Added elsewhere')
        data['title'] = 'Stale update'
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 409)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'Before')
