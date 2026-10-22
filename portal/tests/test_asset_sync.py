import hashlib
import json
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from artists.models import Artist, Organization
from catalog.models import Asset, AssetVersion
from publishing.models import Website
from scripts.sync_catalog_assets import export_bundle, sync_bundle


class AssetSyncTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        config=override_settings(MEDIA_ROOT=str(self.root/'media'))
        config.enable();self.addCleanup(config.disable)
        self.org=Organization.objects.get(slug='bounced-studio')
        self.artist=Artist.objects.create(organization=self.org,name='Test Artist',slug='sync-test')
        self.asset=Asset.objects.create(organization=self.org,source_repository='fixture',source_path='public/lyrics/song-lyrics.txt',mime_type='text/plain',visibility='members')
        self.site=Website.objects.create(organization=self.org,artist=self.artist,domain='sync.test',theme_config={'dezolent_import':{'entity_ids':{'asset:song':str(self.asset.pk)}}})
        content=b'Escaped lyric text <not-html>'
        self.version=AssetVersion(asset=self.asset,original_name='song-lyrics.txt',mime_type='text/plain',byte_size=len(content),sha256=hashlib.sha256(content).hexdigest())
        self.version.file.save('lyrics.txt',ContentFile(content),save=False);self.version.save()
        self.asset.current_version=self.version;self.asset.save()
        self.bundle=self.root/'bundle'
        with override_settings(SETTINGS_MODULE="config.settings.local"):
            export_bundle(self.bundle,self.site.domain,self.artist.slug)
        self.report=self.root/'report.json'

    def test_dry_run_apply_repeat_and_visibility_preservation(self):
        self.asset.current_version=None;self.asset.save()
        dry=sync_bundle(self.bundle,self.report)
        self.asset.refresh_from_db();self.assertEqual(self.asset.display_name,'')
        self.assertEqual(dry['files_needed'],1)
        with self.assertRaises(CommandError):
            sync_bundle(self.bundle,self.report,apply=True,expected_sha256='wrong')
        applied=sync_bundle(self.bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        self.asset.refresh_from_db()
        self.assertEqual(applied['files_copied'],1)
        self.assertEqual(self.asset.visibility,'members')
        self.assertEqual(self.asset.display_name,'song-lyrics.txt')
        self.assertEqual(self.asset.current_version.original_name,'song-lyrics.txt')
        self.assertEqual(self.asset.category,'lyrics_text')
        self.assertTrue(self.asset.alt_text and self.asset.description)
        with self.asset.current_version.file.open('rb') as f:
            self.assertIn(b'Escaped lyric text',f.read())
        repeat=sync_bundle(self.bundle,self.report)
        self.assertEqual((repeat['metadata_updates'],repeat['files_needed']),(0,0))

    def test_editorial_upload_and_metadata_survive_and_hash_drift_aborts(self):
        self.asset.display_name='Artist chosen name';self.asset.alt_text='Chosen alt'
        self.asset.save();original_id=self.asset.current_version_id
        dry=sync_bundle(self.bundle,self.report)
        self.asset.description='Changed since review';self.asset.save()
        with self.assertRaises(CommandError):
            sync_bundle(self.bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        fresh=sync_bundle(self.bundle,self.report)
        sync_bundle(self.bundle,self.report,apply=True,expected_sha256=fresh['sha256'])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_version_id,original_id)
        self.assertEqual(self.asset.display_name,'Artist chosen name')
        self.assertEqual(self.asset.alt_text,'Chosen alt')
        self.assertEqual(self.asset.description,'Changed since review')

    def test_tampered_file_or_wrong_tenant_aborts_without_metadata_write(self):
        document=json.loads((self.bundle/'manifest.json').read_text())
        file=self.bundle/'files'/document['assets'][0]['file']['sha256']/document['assets'][0]['file']['original_name']
        file.write_bytes(b'changed')
        with self.assertRaises(CommandError):sync_bundle(self.bundle,self.report)
        self.asset.refresh_from_db();self.assertFalse(self.asset.display_name)
        file.write_bytes(b'Escaped lyric text <not-html>')
        other=Organization.objects.create(name='Elsewhere',slug='sync-other')
        Asset.objects.filter(pk=self.asset.pk).update(organization=other)
        with self.assertRaises(CommandError):sync_bundle(self.bundle,self.report)
        self.asset.refresh_from_db();self.assertFalse(self.asset.display_name)

    @override_settings(PORTAL_ARTIST_STORAGE_QUOTA=1)
    def test_quota_failure_does_not_copy_or_edit(self):
        self.asset.current_version=None;self.asset.save()
        dry=sync_bundle(self.bundle,self.report)
        with self.assertRaises(CommandError):
            sync_bundle(self.bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.current_version_id)
        self.assertFalse(self.asset.display_name)

    def test_storage_failure_rolls_back_db_and_cleans_new_files(self):
        from unittest.mock import patch
        from portal.local_media import install_media
        self.asset.current_version=None;self.asset.save()
        before=set((self.root/'media').rglob('*'))
        dry=sync_bundle(self.bundle,self.report)
        def fail_after_copy(*args):
            install_media(*args)
            raise OSError('Simulated storage failure')
        with patch('portal.local_media.install_media',side_effect=fail_after_copy):
            with self.assertRaises(OSError):
                sync_bundle(self.bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.current_version_id)
        self.assertFalse(self.asset.display_name)
        self.assertFalse([p for p in (self.root/'media').rglob('*') if p.is_file() and p not in before])

    def test_extensionless_json_and_empty_source_placeholder_transfer(self):
        from portal.asset_display import text_preview
        mapping=self.site.theme_config['dezolent_import']['entity_ids']
        extra=[]
        for name,content in [('api-catalog',b'{"name":"<schema>"}'),('.gitkeep',b'')]:
            asset=Asset.objects.create(organization=self.org,source_repository='fixture',source_path='public/'+name,mime_type='application/octet-stream')
            version=AssetVersion(asset=asset,original_name=name,mime_type='application/octet-stream',byte_size=len(content),sha256=hashlib.sha256(content).hexdigest())
            version.file.save(name,ContentFile(content),save=False);version.save()
            asset.current_version=version;asset.save()
            mapping['asset:'+name]=str(asset.pk);extra.append(asset)
        self.site.save()
        bundle=self.root/'extended'
        with override_settings(SETTINGS_MODULE='config.settings.local'):
            export_bundle(bundle,self.site.domain,self.artist.slug)
        for asset in extra:
            asset.current_version=None;asset.save()
        dry=sync_bundle(bundle,self.report)
        sync_bundle(bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        for asset in extra:asset.refresh_from_db()
        self.assertEqual(extra[0].current_version.mime_type,'application/json')
        self.assertIn('<schema>',text_preview(extra[0])['text'])
        self.assertEqual(extra[1].current_version.byte_size,0)
        self.assertEqual(extra[1].category,'website_asset')

    def test_remote_image_reference_gets_inferred_mime_without_fetching_or_copying(self):
        asset=Asset.objects.create(organization=self.org,source_url='https://example.test/article.webp')
        self.site.theme_config['dezolent_import']['entity_ids']['remote_asset:article']=str(asset.pk)
        self.site.save()
        bundle=self.root/'remote'
        with override_settings(SETTINGS_MODULE='config.settings.local'):
            export_bundle(bundle,self.site.domain,self.artist.slug)
        dry=sync_bundle(bundle,self.report)
        self.assertEqual(dry['files_needed'],0)
        sync_bundle(bundle,self.report,apply=True,expected_sha256=dry['sha256'])
        asset.refresh_from_db()
        self.assertEqual(asset.mime_type,'image/webp')
        self.assertEqual(asset.display_name,'article.webp')
        self.assertFalse(asset.current_version_id)
