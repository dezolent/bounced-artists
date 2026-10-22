"""Focused review regressions for cross-request tokens and relationship scope."""
from unittest.mock import patch

from django.test import TestCase

from catalog import models as c
from portal.forms import stamp
from portal.tests import test_catalog_editor_extended as helpers
from rights import models as r


class CatalogEditorReviewTests(TestCase):
    # Reuse fixture/payload helpers without inheriting the extended test cases.
    setUp = helpers.CatalogEditorExtendedTests.setUp
    grant = helpers.CatalogEditorExtendedTests.grant
    edit_url = helpers.CatalogEditorExtendedTests.edit_url
    payload = helpers.CatalogEditorExtendedTests.payload
    section = helpers.CatalogEditorExtendedTests.section
    row = helpers.CatalogEditorExtendedTests.row
    assert_saved = helpers.CatalogEditorExtendedTests.assert_saved
    work_link = helpers.CatalogEditorExtendedTests.work_link
    party = helpers.CatalogEditorExtendedTests.party
    release = helpers.CatalogEditorExtendedTests.release

    def test_existing_fanlink_destination_may_keep_its_optional_label_blank(self):
        from artists.models import Service
        from publishing.models import Website,FanlinkPage,FanlinkDestination
        service = Service.objects.create(name='Fixture platform',slug='fixture-platform')
        platform = c.PlatformObject.objects.create(service=service,object_type='recording',url='https://music.example.test/track')
        c.RecordingPlatformMatch.objects.create(recording=self.song,platform_object=platform)
        website = Website.objects.create(organization=self.org,artist=self.artist,domain='fanlink.example.test')
        fanlink = FanlinkPage.objects.create(website=website,recording=self.song,slug='song',title='Song')
        destination = FanlinkDestination.objects.create(fanlink_page=fanlink,platform_object=platform)
        data, _ = self.payload()
        data['title'] = 'Parent edit with unnamed destination'
        self.assert_saved(self.client.post(self.url,data))
        destination.refresh_from_db()
        self.assertEqual(destination.label,'')

    def test_elapsed_signing_timestamp_does_not_rewrite_unchanged_verified_child(self):
        identifier = c.RecordingIdentifier.objects.create(recording=self.song, scheme='isrc', value='Keep verified', verification_status='verified')
        original_update = identifier.updated_at
        with patch('django.core.signing.time.time', return_value=1800000000):
            data, _ = self.payload()
        data['title'] = 'Only parent changed across requests'
        with patch('django.core.signing.time.time', return_value=1800000061):
            response = self.client.post(self.url, data)
        self.assert_saved(response)
        self.song.refresh_from_db()
        identifier.refresh_from_db()
        self.assertEqual(self.song.title, 'Only parent changed across requests')
        self.assertEqual(identifier.verification_status, 'verified')
        self.assertEqual(identifier.updated_at, original_update)

    def test_nested_only_edit_keeps_unchanged_verified_work_relationship(self):
        work,link = self.work_link()
        link.verification_status = 'verified'
        link.save()
        original_update = link.updated_at
        contributor = r.WorkContributor.objects.create(work=work,party=self.party(),role='writer')
        with patch('django.core.signing.time.time',return_value=1800000000):
            data,editor = self.payload()
        parent = self.row(self.section(editor,'works'),link)
        nested = next(section for section in parent.nested_sections if section.kind=='contributors')
        child = self.row(nested,contributor)
        data[f'{child.prefix}-role'] = 'composer'
        with patch('django.core.signing.time.time',return_value=1800000061):
            self.assert_saved(self.client.post(self.url,data))
        link.refresh_from_db()
        contributor.refresh_from_db()
        self.assertEqual(contributor.role,'composer')
        self.assertEqual(link.verification_status,'verified')
        self.assertEqual(link.updated_at,original_update)

    def test_switching_work_with_existing_nested_contributor_edit_rejects_atomically(self):
        work_a, link = self.work_link()
        contributor = r.WorkContributor.objects.create(work=work_a, party=self.party(), role='writer')
        work_b = r.MusicalWork.objects.create(organization=self.org, title='Composition B', iswc='T-B')
        self.grant(work_b, 'work')
        data, editor = self.payload()
        form = self.row(self.section(editor, 'works'), link)
        nested = next(section for section in form.nested_sections if section.kind == 'contributors')
        child = self.row(nested, contributor)
        data.update(title='Must roll back', **{
            f'{form.prefix}-work': str(work_b.pk),
            f'{form.prefix}-work_title': work_b.title,
            f'{form.prefix}-work_iswc': work_b.iswc,
            # An authorized user can obtain the chosen work's valid revision.
            # Avoid letting an unrelated stale token mask UUID reparenting.
            f'{form.prefix}-work_version': stamp(work_b),
            f'{child.prefix}-role': 'composer',
        })
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        for obj in (self.song, link, contributor, work_a, work_b):
            obj.refresh_from_db()
        self.assertEqual(self.song.title, 'Original song')
        self.assertEqual(link.work_id, work_a.pk)
        self.assertEqual(contributor.work_id, work_a.pk)
        self.assertEqual(contributor.role, 'writer')
        self.assertEqual((work_a.title, work_b.title), ('Composition', 'Composition B'))
        self.assertEqual(r.WorkContributor.objects.filter(work=work_b).count(), 0)

    def test_release_title_edit_preserves_read_only_release_group(self):
        release = self.release()
        group = c.ReleaseGroup.objects.create(organization=self.org, title='Read-only release group')
        self.grant(group, 'release_group', capability='read')
        release.release_group = group
        release.save()
        original_group_update = group.updated_at
        url = self.edit_url('releases', release)
        data, _ = self.payload(url)
        data['title'] = 'Renamed edition'
        response = self.client.post(url, data)
        self.assert_saved(response)
        release.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(release.title, 'Renamed edition')
        self.assertEqual(release.release_group_id, group.pk)
        self.assertEqual(group.title, 'Read-only release group')
        self.assertEqual(group.updated_at, original_group_update)
