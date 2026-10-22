from django.core.exceptions import SuspiciousOperation
from django.test import SimpleTestCase,RequestFactory
from portal.uploads import BoundedUploadHandler


class UploadBoundsTests(SimpleTestCase):
    def test_catalog_editor_accepts_bounded_multiple_files(self):
        request=RequestFactory().post('/app/artists/example/data/recordings/example/edit/')
        handler=BoundedUploadHandler(request)
        for index in range(12):
            handler.new_file(f'assets-{index}-upload',f'{index}.txt','text/plain',None,None)
        with self.assertRaises(SuspiciousOperation):
            handler.new_file('extra','too-many.txt','text/plain',None,None)

    def test_second_file_is_rejected_before_spooling(self):
        request=RequestFactory().get('/app/artists/example/data/assets/new/')
        handler=BoundedUploadHandler(request)
        handler.new_file('upload','first.wav','audio/wav',None,None)
        handler.receive_data_chunk(b'first chunk',0)
        with self.assertRaises(SuspiciousOperation):
            handler.new_file('extra','second.wav','audio/wav',None,None)
