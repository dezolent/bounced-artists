from django.core.files.uploadhandler import FileUploadHandler
from django.core.exceptions import SuspiciousOperation
import re


class BoundedUploadHandler(FileUploadHandler):
    """Stop receiving a file before buffering an unbounded multipart body."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.received=0
        self.file_count=0
        self.max_files=12 if re.fullmatch(r'/app/artists/[^/]+/data/(recordings|releases)/(new/|[^/]+/edit/)',self.request.path) else 1

    def handle_raw_input(self,input_data,META,content_length,boundary,encoding=None):
        if content_length and content_length>252*1024*1024:
            raise SuspiciousOperation('Upload request exceeds the permitted size.')

    def new_file(self,*args,**kwargs):
        super().new_file(*args,**kwargs)
        self.file_count+=1
        if self.file_count>self.max_files:
            raise SuspiciousOperation(f'Upload at most {self.max_files} file(s) per request.')

    def receive_data_chunk(self,raw_data,start):
        self.received+=len(raw_data)
        if self.received>250*1024*1024:
            raise SuspiciousOperation('Upload request exceeds the permitted size.')
        return raw_data

    def file_complete(self,file_size):
        return None
