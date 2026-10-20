from django.core.exceptions import SuspiciousOperation
from django.test import SimpleTestCase,RequestFactory
from portal.uploads import BoundedUploadHandler


class UploadBoundsTests(SimpleTestCase):
    def test_second_file_is_rejected_before_spooling(self):
        request=RequestFactory().get('/app/artists/example/data/assets/new/')
        handler=BoundedUploadHandler(request)
        handler.new_file('upload','first.wav','audio/wav',None,None)
        handler.receive_data_chunk(b'first chunk',0)
        with self.assertRaises(SuspiciousOperation):
            handler.new_file('extra','second.wav','audio/wav',None,None)
