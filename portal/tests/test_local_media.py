import hashlib
import io
import tempfile
from pathlib import Path
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from artists.models import Artist,Organization
from catalog.models import Asset,AssetVersion
from portal.local_media import verified_source, install_media


class LocalMediaTests(TestCase):
    def setUp(self):
        self.artist=Artist.objects.create(organization=Organization.objects.get(slug='bounced-studio'),name='Local Artist',slug='local-artist')
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'public').mkdir()
        self.asset=Asset.objects.create(organization=self.artist.organization,source_repository='local-source',source_path='public/cover.png')

    def image(self):
        from PIL import Image
        output=io.BytesIO();Image.new('RGB',(32,24),'blue').save(output,format='PNG')
        path=self.root/'public'/'cover.png';path.write_bytes(output.getvalue())
        return {'source_path':'public/cover.png','bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime_type':'image/png'}

    def test_verified_image_gets_private_original_and_idempotent_local_preview(self):
        row=self.image();path=verified_source(self.root,row)
        with override_settings(MEDIA_ROOT=self.root/'storage'):
            self.assertTrue(install_media(self.artist,self.asset,path,row))
            self.asset.refresh_from_db()
            self.assertTrue(self.asset.current_version.preview_file)
            self.assertEqual((self.asset.current_version.preview_width,self.asset.current_version.preview_height),(32,24))
            self.assertFalse(install_media(self.artist,self.asset,path,row))
            self.assertEqual(AssetVersion.objects.count(),1)
            self.assertFalse(self.asset.rights_approved)

    def test_changed_source_and_path_escape_fail_before_copy(self):
        row=self.image()
        (self.root/'public'/'cover.png').write_bytes(b'changed')
        with self.assertRaises(ValidationError):
            verified_source(self.root,row)
        row['source_path']='../outside.png'
        with self.assertRaises(ValidationError):
            verified_source(self.root,row)
        self.assertEqual(AssetVersion.objects.count(),0)

    def test_trusted_svg_original_is_download_only_and_never_a_public_preview(self):
        path=self.root/'public'/'brand.svg';path.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        row={'source_path':'public/brand.svg','bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime_type':'image/svg+xml'}
        with override_settings(MEDIA_ROOT=self.root/'storage'):
            self.assertTrue(install_media(self.artist,self.asset,verified_source(self.root,row),row))
            self.asset.refresh_from_db()
            self.assertFalse(self.asset.current_version.preview_file)
            self.assertTrue(self.asset.current_version.file)

    def test_editorial_checksum_is_not_replaced_by_source_media(self):
        row=self.image();path=verified_source(self.root,row)
        self.asset.sha256='f'*64;self.asset.save()
        with override_settings(MEDIA_ROOT=self.root/'storage'),self.assertRaises(ValidationError):
            install_media(self.artist,self.asset,path,row)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.sha256,'f'*64)
        self.assertEqual(AssetVersion.objects.count(),0)
