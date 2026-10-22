"""Catalog editor integration contracts for existing and nested relationship rows."""
import hashlib
import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization, Service
from catalog import models as c
from portal.catalog_editor import CatalogEditor
from portal.models import EditorialChange
from portal.querysets import scoped
from provenance.models import SourceRecord
from publishing.models import FanlinkDestination, FanlinkPage, Website
from rights import models as r


def field_value(value):
    """Serialize initial form values as a browser submission, retaining FK UUIDs."""
    if hasattr(value, 'pk'):
        return str(value.pk)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if value is True:
        return 'on'
    if value is False or value is None:
        return ''
    return str(value)


def serialize_form(form, target, rename=lambda name: name):
    # Include disabled initial values too; tests can deliberately tamper with them.
    for bound in form:
        if isinstance(bound.field, forms.FileField):
            continue
        target[rename(bound.html_name)] = field_value(bound.value())
    for nested in getattr(form, 'nested_sections', ()):
        serialize_section(nested, target, rename)


def serialize_section(section, target, rename=lambda name: name):
    serialize_form(section.formset.management_form, target, rename)
    for form in section.formset:
        serialize_form(form, target, rename)


def append_row(data, section, *, prefix=None, **values):
    """Start with the editor's actual empty row, including nested management forms."""
    prefix = prefix or section.prefix
    index = int(data.get(f'{prefix}-TOTAL_FORMS', 0))

    def rename(name):
        return name.replace(section.prefix, prefix, 1).replace('__prefix__', str(index))

    serialize_form(section.empty_form, data, rename)
    data[f'{prefix}-TOTAL_FORMS'] = str(index + 1)
    row_prefix = f'{prefix}-{index}'
    for name, value in values.items():
        data[f'{row_prefix}-{name}'] = field_value(value)
    return row_prefix


@override_settings(PORTAL_UPLOADS_ENABLED=True)
class CatalogEditorExtendedTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug='bounced-studio')
        self.artist = Artist.objects.create(organization=self.org, name='Extended editor', slug='extended-editor', management_status='managed')
        self.user = get_user_model().objects.create_user(username='extended-editor')
        ArtistMembership.objects.create(artist=self.artist, user=self.user, role='owner')
        self.song = c.Recording.objects.create(organization=self.org, title='Original song')
        self.grant(self.song, 'recording')
        self.client.force_login(self.user)
        self.url = self.edit_url('recordings', self.song)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.temp.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)

    def grant(self, obj, kind, capability='manage'):
        return ArtistResourceGrant.objects.create(artist=self.artist, capability=capability, **{kind: obj})

    def edit_url(self, kind, obj):
        return reverse('portal:edit', args=[self.artist.pk, kind, obj.pk])

    def payload(self, url=None):
        response = self.client.get(url or self.url)
        self.assertEqual(response.status_code, 200)
        editor = response.context['editor']
        data = {}
        serialize_form(editor.form, data)
        for section in editor.sections:
            serialize_section(section, data)
        return data, editor

    def section(self, editor, prefix):
        return next(section for section in editor.sections if section.prefix == prefix)

    def row(self, section, obj):
        return next(form for form in section.formset if form.catalog_object.pk == obj.pk)

    def assert_parent_row_unchanged(self, data, section_prefix, obj):
        bound = CatalogEditor(self.user, self.artist, 'recordings', instance=self.song, data=data)
        parent_form = self.row(self.section(bound, section_prefix), obj)
        changed = set(parent_form.changed_data) - {'row_id', 'version'}
        self.assertFalse(changed, sorted(changed))

    def assert_saved(self, response):
        errors = None
        if response.context and 'editor' in response.context:
            editor = response.context['editor']
            errors = {'parent': editor.form.errors, 'sections': {section.prefix: section.formset.errors for section in editor.sections}}
        self.assertEqual(response.status_code, 302, errors)

    def source(self):
        return SourceRecord.objects.create(organization=self.org, collector='fixture', collection_method='manual', object_type='recording', payload_hash='a' * 64, payload_json={'fixture': 'preserve'}, observed_at=timezone.now())

    def work_link(self, capability='manage'):
        work = r.MusicalWork.objects.create(organization=self.org, title='Composition', iswc='T-original')
        self.grant(work, 'work', capability)
        link = r.RecordingWork.objects.create(recording=self.song, work=work)
        return work, link

    def party(self, name='Writer'):
        party = r.Party.objects.create(organization=self.org, display_name=name, party_type='person')
        self.grant(party, 'party')
        return party

    def release(self):
        release = c.Release.objects.create(organization=self.org, title='Original release')
        self.grant(release, 'release')
        return release

    def text_asset(self, content=b'Original lyrics', name='lyrics.txt'):
        asset = c.Asset.objects.create(organization=self.org, storage_key='fixture/' + name, display_name=name, category='lyrics_text', visibility='members', mime_type='text/plain')
        version = c.AssetVersion(asset=asset, original_name=name, byte_size=len(content), mime_type='text/plain', sha256=hashlib.sha256(content).hexdigest())
        version.file.save(name, ContentFile(content), save=False)
        version.save()
        asset.current_version = version
        asset.storage_key = version.file.name
        asset.save()
        self.grant(asset, 'asset')
        return asset, version

    def platform_object(self):
        service = Service.objects.create(name='Extended music', slug='extended-music')
        obj = c.PlatformObject.objects.create(service=service, object_type='recording', url='https://music.example.test/original')
        c.RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj)
        return obj

    def test_existing_identifier_update_preserves_uuid_and_source_evidence(self):
        source = self.source()
        identifier = c.RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Before', source_record=source, verification_status='verified')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'identifiers'), identifier)
        data[f'{form.prefix}-value'] = 'After'
        self.assert_saved(self.client.post(self.url, data))
        identifier.refresh_from_db()
        self.assertEqual(identifier.value, 'After')
        self.assertEqual(identifier.source_record_id, source.pk)
        self.assertEqual(identifier.verification_status, 'unverified')
        self.assertEqual(c.RecordingIdentifier.objects.filter(recording=self.song).count(), 1)
        source.refresh_from_db()
        self.assertEqual(source.payload_json, {'fixture': 'preserve'})

    def test_existing_identifier_removal_retains_source_row_and_hides_association(self):
        source = self.source()
        identifier = c.RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Keep original', source_record=source)
        data, editor = self.payload()
        form = self.row(self.section(editor, 'identifiers'), identifier)
        data[f'{form.prefix}-DELETE'] = 'on'
        self.assert_saved(self.client.post(self.url, data))
        identifier.refresh_from_db()
        self.assertEqual(identifier.value, 'Keep original')
        self.assertEqual(identifier.source_record_id, source.pk)
        self.assertFalse(scoped(self.user, self.artist, 'recording-identifiers').filter(pk=identifier.pk).exists())
        self.assertTrue(EditorialChange.objects.filter(object_id=str(identifier.pk), action='delete').exists())
        self.assertTrue(SourceRecord.objects.filter(pk=source.pk).exists())

    def test_read_only_work_relationship_ignores_tampered_fields_without_losing_row(self):
        work, link = self.work_link('read')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        self.assertTrue(form.read_only)
        data.update(title='Editable parent', **{f'{form.prefix}-work_title': 'Unauthorized work title', f'{form.prefix}-DELETE': 'on'})
        self.assert_saved(self.client.post(self.url, data))
        work.refresh_from_db()
        self.song.refresh_from_db()
        self.assertEqual(work.title, 'Composition')
        self.assertEqual(self.song.title, 'Editable parent')
        self.assertTrue(scoped(self.user, self.artist, 'recording-works').filter(pk=link.pk).exists())

    def test_foreign_nested_contributor_id_rejects_entire_submission(self):
        work, link = self.work_link()
        other_work = r.MusicalWork.objects.create(organization=self.org, title='Other composition')
        self.grant(other_work, 'work')
        party = self.party()
        foreign = r.WorkContributor.objects.create(work=other_work, party=party, role='writer')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        nested = next(section for section in form.nested_sections if section.kind == 'contributors')
        append_row(data, nested, row_id=foreign.pk, party=party.pk, role='composer')
        data['title'] = 'Must roll back'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.song.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(self.song.title, 'Original song')
        self.assertEqual(foreign.role, 'writer')
        self.assertFalse(r.WorkContributor.objects.filter(work=work).exists())

    def test_release_creation_creates_inline_song_and_track_with_explicit_grants(self):
        url = reverse('portal:create', args=[self.artist.pk, 'releases'])
        data, editor = self.payload(url)
        data.update(title='New release', release_type='ep', date_precision='unknown')
        append_row(data, self.section(editor, 'tracks'), new_recording='Inline song', disc_number=1, track_number=1, display_title='Track display', duration_ms=123000)
        append_row(data, self.section(editor, 'tracks'), recording=self.song.pk, disc_number=2, track_number=1)
        self.assert_saved(self.client.post(url, data))
        release = c.Release.objects.get(title='New release')
        track = c.ReleaseTrack.objects.get(release=release, disc_number=1)
        self.assertEqual(track.recording.title, 'Inline song')
        self.assertEqual(track.display_title, 'Track display')
        self.assertEqual(track.duration_ms, 123000)
        self.assertEqual(c.ReleaseTrack.objects.get(release=release, disc_number=2).recording_id, self.song.pk)
        self.assertEqual(release.tracks.count(), 2)
        self.assertTrue(scoped(self.user, self.artist, 'recordings', write=True).filter(pk=track.recording_id).exists())
        self.assertTrue(scoped(self.user, self.artist, 'releases', write=True).filter(pk=release.pk).exists())

    def test_track_position_swap_preserves_track_uuids_and_recordings(self):
        release = self.release()
        second_song = c.Recording.objects.create(organization=self.org, title='Second song')
        self.grant(second_song, 'recording')
        first = c.ReleaseTrack.objects.create(release=release, recording=self.song, track_number=1)
        second = c.ReleaseTrack.objects.create(release=release, recording=second_song, track_number=2)
        url = self.edit_url('releases', release)
        data, editor = self.payload(url)
        tracks = self.section(editor, 'tracks')
        data[f'{self.row(tracks, first).prefix}-track_number'] = '2'
        data[f'{self.row(tracks, second).prefix}-track_number'] = '1'
        self.assert_saved(self.client.post(url, data))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.track_number, second.track_number), (2, 1))
        self.assertEqual((first.recording_id, second.recording_id), (self.song.pk, second_song.pk))
        self.assertEqual(c.ReleaseTrack.objects.filter(release=release).count(), 2)

    def test_nested_only_contributor_edit_saves_without_parent_row_change(self):
        work, link = self.work_link()
        contributor = r.WorkContributor.objects.create(work=work, party=self.party(), role='writer')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        nested = next(section for section in form.nested_sections if section.kind == 'contributors')
        child = self.row(nested, contributor)
        data[f'{child.prefix}-role'] = 'composer'
        self.assert_parent_row_unchanged(data, 'works', link)
        self.assert_saved(self.client.post(self.url, data))
        contributor.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(contributor.role, 'composer')
        self.assertEqual(link.work_id, work.pk)
        self.assertEqual(r.WorkContributor.objects.filter(work=work).count(), 1)

    def test_invalid_nested_contributor_rolls_back_parent_work_and_other_child_edits(self):
        work, link = self.work_link()
        contributor = r.WorkContributor.objects.create(work=work, party=self.party(), role='writer')
        identifier = c.RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Before')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        nested = next(section for section in form.nested_sections if section.kind == 'contributors')
        child = self.row(nested, contributor)
        data.update(title='Must roll back', **{f'{form.prefix}-work_title': 'Work must roll back', f'{child.prefix}-role': '', f'{self.row(self.section(editor, "identifiers"), identifier).prefix}-value': 'Must roll back'})
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        for obj in (work, self.song, identifier, contributor):
            obj.refresh_from_db()
        self.assertEqual((work.title, self.song.title, identifier.value, contributor.role), ('Composition', 'Original song', 'Before', 'writer'))

    def test_nested_work_identifier_edit_resets_verification_and_preserves_identity(self):
        work, link = self.work_link()
        identifier = r.WorkIdentifier.objects.create(work=work, scheme='iswc', value='T-before', verification_status='verified')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        nested = next(section for section in form.nested_sections if section.kind == 'work-identifiers')
        child = self.row(nested, identifier)
        data[f'{child.prefix}-value'] = 'T-after'
        self.assert_parent_row_unchanged(data, 'works', link)
        self.assert_saved(self.client.post(self.url, data))
        identifier.refresh_from_db()
        self.assertEqual(identifier.value, 'T-after')
        self.assertEqual(identifier.verification_status, 'unverified')
        self.assertEqual(work.identifiers.count(), 1)

    def test_lyrics_upload_creates_private_version_and_attachment(self):
        data, editor = self.payload()
        row = append_row(data, self.section(editor, 'assets'), purpose='lyrics', category='lyrics_text', visibility='members', display_name='Uploaded lyrics')
        data[f'{row}-upload'] = SimpleUploadedFile('lyrics.txt', b'First line\nSecond line', content_type='text/plain')
        self.assert_saved(self.client.post(self.url, data))
        link = c.RecordingAsset.objects.get(recording=self.song, purpose='lyrics')
        self.assertEqual(link.asset.visibility, 'members')
        self.assertEqual(link.asset.category, 'lyrics_text')
        self.assertFalse(link.asset.rights_approved)
        with link.asset.current_version.file.open('rb') as stream:
            self.assertEqual(stream.read(), b'First line\nSecond line')
        self.assertTrue(scoped(self.user, self.artist, 'assets', write=True).filter(pk=link.asset_id).exists())

    def test_editing_lyrics_creates_new_version_preserving_original_and_other_variant(self):
        asset, original = self.text_asset()
        alternate, alternate_version = self.text_asset(b'Alternate lyrics', 'alternate.txt')
        link = c.RecordingAsset.objects.create(recording=self.song, asset=asset, purpose='lyrics')
        c.RecordingAsset.objects.create(recording=self.song, asset=alternate, purpose='lyrics')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'assets'), link)
        data[f'{form.prefix}-text_content'] = '<script>New escaped lyric</script>\nComplete new lyrics'
        self.assert_saved(self.client.post(self.url, data))
        asset.refresh_from_db()
        alternate.refresh_from_db()
        link.refresh_from_db()
        self.assertNotEqual(asset.current_version_id, original.pk)
        self.assertEqual(asset.versions.count(), 2)
        self.assertEqual(link.asset_id, asset.pk)
        self.assertEqual(alternate.current_version_id, alternate_version.pk)
        with original.file.open('rb') as stream:
            self.assertEqual(stream.read(), b'Original lyrics')
        with asset.current_version.file.open('rb') as stream:
            self.assertEqual(stream.read(), b'<script>New escaped lyric</script>\nComplete new lyrics')
        response = self.client.get(reverse('portal:recording', args=[self.artist.pk, self.song.pk]))
        self.assertContains(response, '&lt;script&gt;New escaped lyric&lt;/script&gt;')
        self.assertNotContains(response, '<script>New escaped lyric</script>')

    def test_invalid_lyrics_upload_rolls_back_parent_and_leaves_no_file_reference(self):
        data, editor = self.payload()
        data['title'] = 'Must roll back'
        row = append_row(data, self.section(editor, 'assets'), purpose='lyrics', category='lyrics_text', visibility='members')
        data[f'{row}-upload'] = SimpleUploadedFile('lyrics.txt', b'Binary\x00payload', content_type='text/plain')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'Original song')
        self.assertFalse(c.RecordingAsset.objects.filter(recording=self.song).exists())
        self.assertEqual(c.AssetVersion.objects.count(), 0)
        self.assertEqual([path for path in Path(self.temp.name).rglob('*') if path.is_file()], [])

    def test_fanlink_creation_saves_nested_destination(self):
        website = Website.objects.create(organization=self.org, artist=self.artist, domain='extended-editor.test')
        platform = self.platform_object()
        data, editor = self.payload()
        section = self.section(editor, 'fanlinks')
        prefix = append_row(data, section, website=website.pk, slug='song', title='Song destinations')
        nested = section.empty_form.nested_sections[0]
        append_row(data, nested, prefix=f'{prefix}-destinations', platform_object=platform.pk, label='Listen', sort_order=1, enabled=True)
        self.assert_saved(self.client.post(self.url, data))
        fanlink = FanlinkPage.objects.get(recording=self.song, slug='song')
        destination = fanlink.destinations.get()
        self.assertEqual(fanlink.website_id, website.pk)
        self.assertEqual(destination.platform_object_id, platform.pk)
        self.assertEqual((destination.label, destination.sort_order, destination.enabled), ('Listen', 1, True))

    def test_nested_only_fanlink_destination_edit_preserves_fanlink_uuid(self):
        website = Website.objects.create(organization=self.org, artist=self.artist, domain='extended-editor.test')
        platform = self.platform_object()
        fanlink = FanlinkPage.objects.create(website=website, recording=self.song, slug='song', title='Song destinations')
        destination = FanlinkDestination.objects.create(fanlink_page=fanlink, platform_object=platform, label='Before', sort_order=1)
        data, editor = self.payload()
        form = self.row(self.section(editor, 'fanlinks'), fanlink)
        nested = form.nested_sections[0]
        child = self.row(nested, destination)
        data.update(**{f'{child.prefix}-label': 'After', f'{child.prefix}-enabled': ''})
        self.assert_parent_row_unchanged(data, 'fanlinks', fanlink)
        self.assert_saved(self.client.post(self.url, data))
        destination.refresh_from_db()
        self.assertEqual(destination.label, 'After')
        self.assertFalse(destination.enabled)
        self.assertEqual(destination.fanlink_page_id, fanlink.pk)
        self.assertEqual(FanlinkPage.objects.filter(recording=self.song).count(), 1)

    def test_read_only_parent_grant_cannot_open_or_post_editor(self):
        ArtistResourceGrant.objects.filter(recording=self.song, artist=self.artist).update(capability='read')
        self.assertEqual(self.client.get(self.url).status_code, 404)
        response = self.client.post(self.url, {'title': 'Unauthorized'})
        self.assertEqual(response.status_code, 404)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'Original song')

    def test_parent_title_edit_does_not_invalidate_unchanged_verified_identifier(self):
        identifier = c.RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Keep verified', verification_status='verified')
        previous_update = identifier.updated_at
        data, _ = self.payload()
        data['title'] = 'Only parent changed'
        self.assert_saved(self.client.post(self.url, data))
        identifier.refresh_from_db()
        self.assertEqual(identifier.verification_status, 'verified')
        self.assertEqual(identifier.updated_at, previous_update)

    def test_track_removal_and_position_reuse_preserve_removed_track_uuid_and_position(self):
        release = self.release()
        other_song = c.Recording.objects.create(organization=self.org, title='Retained song')
        self.grant(other_song, 'recording')
        # The moved row sorts before the removed row, so request order cannot
        # accidentally make this pass without processing removals first.
        removed = c.ReleaseTrack.objects.create(id=UUID(int=2), release=release, recording=self.song, track_number=1)
        moved = c.ReleaseTrack.objects.create(id=UUID(int=1), release=release, recording=other_song, track_number=2)
        url = self.edit_url('releases', release)
        data, editor = self.payload(url)
        tracks = self.section(editor, 'tracks')
        data[f'{self.row(tracks, removed).prefix}-DELETE'] = 'on'
        data[f'{self.row(tracks, moved).prefix}-track_number'] = '1'
        self.assert_saved(self.client.post(url, data))
        removed.refresh_from_db()
        moved.refresh_from_db()
        self.assertEqual((removed.disc_number, removed.track_number), (1, 1))
        self.assertIsNotNone(removed.archived_at)
        self.assertEqual(moved.track_number, 1)
        self.assertIsNone(moved.archived_at)
        self.assertTrue(c.Recording.objects.filter(pk=self.song.pk).exists())
        self.assertFalse(scoped(self.user, self.artist, 'tracks').filter(pk=removed.pk).exists())
        self.assertEqual(list(scoped(self.user, self.artist, 'tracks').filter(release=release).values_list('id', flat=True)), [moved.pk])
