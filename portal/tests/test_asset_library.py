import hashlib
import io
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command,CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.templatetags.static import static
from rest_framework.test import APIClient

from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization
from catalog.models import Asset, AssetVersion
from portal.forms import make_form, stamp
from portal.querysets import scoped
from catalog.brand_library import GROUPS,brand_logo


class AssetLibraryTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Owner',slug='asset-owner',management_status='managed')
        self.other=Artist.objects.create(organization=self.org,name='Other',slug='asset-other',management_status='managed')
        self.user=get_user_model().objects.create_user(username='asset-owner')
        ArtistMembership.objects.create(artist=self.artist,user=self.user,role='owner')
        self.client.force_login(self.user)
        self.api=APIClient();self.api.force_authenticate(self.user)
        self.url=reverse('portal:resources',args=[self.artist.pk,'assets'])
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=override_settings(MEDIA_ROOT=self.temp.name)
        self.settings.enable();self.addCleanup(self.settings.disable)

    def asset(self,path,mime='text/plain',content=None):
        asset=Asset.objects.create(organization=self.org,source_repository='fixture',source_path=path,display_name=Path(path).name,mime_type=mime,source_url='https://example.test/'+Path(path).name)
        ArtistResourceGrant.objects.create(artist=self.artist,asset=asset,capability='manage')
        if content is not None:
            version=AssetVersion(asset=asset,original_name=Path(path).name,mime_type=mime,byte_size=len(content),sha256=hashlib.sha256(content).hexdigest())
            version.file.save('test.txt',ContentFile(content),save=False);version.save()
            asset.current_version=version;asset.save()
        return asset

    def test_text_index_uses_distinct_icons_and_never_an_image(self):
        for path,mime,icon in [('public/lyrics/song-lyrics.txt','text/plain','lyrics'),('public/robots.txt','text/plain','robot'),('public/site.webmanifest','application/manifest+json','file-text')]:
            self.asset(path,mime)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        rows={row['label']:row for row in response.context['rows']}
        for name,icon in [('song-lyrics.txt','lyrics'),('robots.txt','robot'),('site.webmanifest','file-text')]:
            self.assertEqual(rows[name]['icon'],icon)
            self.assertFalse(rows[name]['cover_url'])
        self.assertNotContains(response,'class="resource-thumb"')

    def test_visibility_filter_sort_and_workspace_links_remain_scoped(self):
        for visibility in ('private','members','public'):
            asset=self.asset(f'public/lyrics/{visibility}.txt')
            Asset.objects.filter(pk=asset.pk).update(visibility=visibility,category='lyrics_text')
        foreign=self.asset('public/lyrics/not-this-artist.txt')
        ArtistResourceGrant.objects.filter(asset=foreign).delete()
        ArtistResourceGrant.objects.create(artist=self.other,asset=foreign,capability='manage')
        ArtistMembership.objects.create(artist=self.other,user=self.user,role='viewer')
        response=self.client.get(self.url,{'visibility':'members','category':'lyrics_text','sort':'visibility','q':'.txt'})
        self.assertEqual([row['label'] for row in response.context['rows']],['members.txt'])
        self.assertEqual(response.context['rows'][0]['visibility_label'],'Members')
        self.assertContains(response,'<th scope="col">Visibility</th>',html=True)
        self.assertIn('visibility=members',response.context['type_sort_url'])
        self.assertIn('visibility=members',response.context['pagination_query'])
        other=next(row for row in response.context['artists'] if row.pk==self.other.pk)
        self.assertIn('visibility=members',other.workspace_url)
        self.assertIn('sort=visibility',other.workspace_url)
        response=self.client.get(self.url,{'sort':'-visibility','visibility':'invalid'})
        self.assertEqual([row['visibility'] for row in response.context['rows']],['public','private','members'])

    def test_shared_logos_are_public_and_visibility_filter_does_not_grant_access(self):
        response=self.client.get(self.url,{'library':'shared','visibility':'private'})
        self.assertEqual(response.context['page_obj'].paginator.count,0)
        response=self.client.get(self.url,{'library':'shared','visibility':'public'})
        self.assertEqual(response.context['page_obj'].paginator.count,32)
        self.assertTrue(all(row['visibility_label']=='Public' for row in response.context['rows']))

    def test_category_is_optional_choice_and_writable_without_broadening_api_fields(self):
        asset=self.asset('public/lyrics/song.txt')
        form=make_form('assets',self.user,self.artist,instance=asset)
        self.assertIn('category',form.fields)
        self.assertFalse(form.fields['category'].required)
        self.assertIn(('lyrics_srt','Lyrics · SRT'),list(form.fields['category'].choices))
        endpoint=f'/api/portal/v1/artists/{self.artist.pk}/assets/{asset.pk}/'
        response=self.api.patch(endpoint,{'category':'lyrics_text','version':stamp(asset)},format='json')
        self.assertEqual(response.status_code,200,response.content)
        asset.refresh_from_db();self.assertEqual(asset.category,'lyrics_text')
        response=self.api.patch(endpoint,{'category':'arbitrary','version':stamp(asset)},format='json')
        self.assertEqual(response.status_code,400)

    def test_category_filter_and_type_sort_preserve_search_and_pagination(self):
        cover=self.asset('public/images/covers/cover.webp','image/webp')
        lyric=self.asset('public/lyrics/song.txt')
        Asset.objects.filter(pk=cover.pk).update(category='cover_art')
        Asset.objects.filter(pk=lyric.pk).update(category='lyrics_text')
        response=self.client.get(self.url,{'sort':'type'})
        self.assertEqual([row['label'] for row in response.context['rows']],['cover.webp','song.txt'])
        response=self.client.get(self.url,{'category':'lyrics_text','sort':'-type','q':'song'})
        self.assertEqual([row['label'] for row in response.context['rows']],['song.txt'])
        self.assertContains(response,'Lyrics · text')

    def test_text_detail_is_escaped_and_does_not_fetch_remote_content(self):
        asset=self.asset('public/lyrics/song.txt',content=b'<script>preview-sentinel</script>\nSecond line')
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',asset.pk]))
        self.assertContains(response,'&lt;script&gt;preview-sentinel&lt;/script&gt;')
        self.assertNotContains(response,'<script>preview-sentinel</script>')
        self.assertContains(response,'Second line')
        remote=self.asset('public/robots.txt')
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',remote.pk]))
        self.assertContains(response,'No stored text is available')

    def test_text_preview_is_bounded_missing_file_tolerant_and_artist_scoped(self):
        asset=self.asset('public/lyrics/large.txt',content=b'A'*(256*1024)+b'NEVER-RENDER-TAIL')
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',asset.pk]))
        self.assertNotContains(response,'NEVER-RENDER-TAIL')
        self.assertContains(response,'Preview limited')
        self.assertEqual(self.client.get(reverse('portal:detail',args=[self.other.pk,'assets',asset.pk])).status_code,404)
        asset.current_version.file.delete(save=False)
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',asset.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'No stored text is available')

    def test_shared_brand_sources_leave_artist_scope_but_originals_survive(self):
        brand=self.asset('public/images/social/spotify.webp','image/webp')
        personal=self.asset('public/images/artist/spotify.webp','image/webp')
        for user in (self.user,get_user_model().objects.create_user(username='staff',is_staff=True)):
            self.assertNotIn(brand,scoped(user,self.artist,'assets'))
            self.assertNotIn(brand,scoped(user,self.artist,'assets',write=True))
            self.assertIn(personal,scoped(user,self.artist,'assets'))
        self.assertTrue(Asset.objects.filter(pk=brand.pk).exists())
        self.assertEqual(self.api.get(f'/api/portal/v1/artists/{self.artist.pk}/assets/{brand.pk}/').status_code,404)

    def test_shared_library_is_identical_for_every_workspace_without_artist_grants(self):
        ArtistMembership.objects.create(artist=self.other,user=self.user,role='viewer')
        responses=[self.client.get(reverse('portal:resources',args=[artist.pk,'assets']),{'library':'shared'}) for artist in (self.artist,self.other)]
        self.assertEqual(responses[0].context['page_obj'].paginator.count,32)
        self.assertEqual(responses[1].context['page_obj'].paginator.count,32)
        response=self.client.get(self.url,{'library':'shared','q':'tiktok'})
        self.assertContains(response,'TikTok')
        self.assertContains(response,static('portal/library/social/tiktok.webp'))
        self.assertNotContains(response,'Add record')

    def test_library_files_are_packaged_and_service_lookup_is_allowlisted(self):
        for group,slugs in GROUPS.items():
            for slug in slugs:
                path=finders.find(f'portal/library/{group}/{slug}.webp')
                self.assertTrue(path)
                with Path(path).open('rb') as stream:
                    header=stream.read(12)
                self.assertEqual(header[:4],b'RIFF')
                self.assertEqual(header[8:],b'WEBP')
        self.assertEqual(brand_logo('Spotify'),static('portal/library/platforms/spotify.webp'))
        self.assertFalse(brand_logo('../../private'))
        self.assertFalse(brand_logo('unknown'))

    def test_import_assignment_does_not_reintroduce_artist_brand_grants(self):
        from publishing.models import Website
        brand=self.asset('public/images/platforms/spotify.webp','image/webp')
        lyric=self.asset('public/lyrics/song.txt')
        site=Website.objects.create(artist=self.artist,organization=self.org,domain='asset-import.test',theme_config={'dezolent_import':{'entity_ids':{'asset:brand':str(brand.pk),'asset:lyric':str(lyric.pk)}}})
        report=Path(self.temp.name)/'assign.json'
        call_command('assign_import_catalog',website=site.domain,artist=str(self.artist.pk),report=str(report),stdout=io.StringIO())
        receipt=json.loads(report.read_text())
        self.assertEqual(receipt['grants'],[{'kind':'asset','id':str(lyric.pk)}])
        ArtistResourceGrant.objects.filter(asset=brand).delete()
        call_command('assign_import_catalog',website=site.domain,artist=str(self.artist.pk),report=str(report),apply=True,expected_sha256=receipt['sha256'],stdout=io.StringIO())
        self.assertFalse(ArtistResourceGrant.objects.filter(asset=brand).exists())

    def test_type_filters_survive_pagination_and_workspace_switches(self):
        ArtistMembership.objects.create(artist=self.other,user=self.user,role='viewer')
        response=self.client.get(self.url,{'library':'shared','sort':'-type'})
        self.assertContains(response,'library=shared&amp;page=2')
        self.assertContains(response,'aria-sort="descending"')
        self.assertIn('sort=type',response.context['type_sort_url'])
        target=next(row for row in response.context['artists'] if row.pk==self.other.pk)
        self.assertIn('library=shared',target.workspace_url)
        self.assertIn('sort=-type',target.workspace_url)
        self.assertNotIn('page=',target.workspace_url)

    def test_organization_command_requires_review_and_preserves_originals_and_chosen_categories(self):
        brand=self.asset('public/images/social/spotify.webp','image/webp')
        lyric=self.asset('public/lyrics/song-lyrics.txt')
        chosen=self.asset('public/lyrics/chosen-lyrics.txt')
        Asset.objects.filter(pk=chosen.pk).update(category='document')
        report=Path(self.temp.name)/'report.json'
        call_command('organize_assets',report=str(report),stdout=io.StringIO())
        receipt=json.loads(report.read_text())
        lyric.refresh_from_db();self.assertEqual(lyric.category,'')
        self.assertTrue(ArtistResourceGrant.objects.filter(asset=brand).exists())
        with self.assertRaises(CommandError):
            call_command('organize_assets',report=str(report),apply=True,expected_sha256='incorrect',stdout=io.StringIO())
        call_command('organize_assets',report=str(report),apply=True,expected_sha256=receipt['sha256'],stdout=io.StringIO())
        lyric.refresh_from_db();chosen.refresh_from_db()
        self.assertEqual(lyric.category,'lyrics_text')
        self.assertEqual(chosen.category,'document')
        self.assertFalse(ArtistResourceGrant.objects.filter(asset=brand).exists())
        self.assertTrue(Asset.objects.filter(pk=brand.pk).exists())
        call_command('organize_assets',report=str(report),stdout=io.StringIO())
        repeat=json.loads(report.read_text())
        self.assertEqual(repeat['categories'],[])
        self.assertEqual(repeat['shared_grants'],[])

    @override_settings(PORTAL_UPLOADS_ENABLED=True)
    def test_srt_and_manifest_uploads_are_utf8_attachments_without_image_previews(self):
        from portal.media import prepare_upload
        for filename,mime in [('lyrics.srt','application/x-subrip'),('site.webmanifest','application/manifest+json')]:
            upload=SimpleUploadedFile(filename,b'UTF-8 preview sentinel')
            metadata,_,preview=prepare_upload(upload)
            self.assertEqual(metadata['mime_type'],mime)
            self.assertIsNone(preview)

    def test_extensionless_catalog_text_is_escaped_and_not_executed(self):
        asset=self.asset('public/.well-known/api-catalog','application/octet-stream',content=b'{"value":"<script>"}')
        response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',asset.pk]))
        self.assertContains(response,'&lt;script&gt;')
        self.assertNotContains(response,'<script>')

    def test_asset_detail_uses_saved_alt_text_for_the_preview(self):
        from unittest.mock import patch
        asset=self.asset('public/images/covers/example.webp','image/webp')
        asset.alt_text='Cover artwork for the example recording';asset.save()
        with patch('portal.views.cover_url',return_value='/safe-preview/'):
            response=self.client.get(reverse('portal:detail',args=[self.artist.pk,'assets',asset.pk]))
        self.assertContains(response,'alt="Cover artwork for the example recording"')
