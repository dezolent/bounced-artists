from django.test import SimpleTestCase

from artists.models import Artist, Organization, Service
from catalog.models import Recording
from provenance.models import Connection, EntityTarget, FieldObservation
from portal.operations import object_label


class ProvenanceLabelTests(SimpleTestCase):
    def test_connection_uses_service_auth_and_artist(self):
        obj = Connection(organization=Organization(name='Bounced Studio'), artist=Artist(name='Dezolent'),
            service=Service(name='Spotify'), auth_type='api_key')
        self.assertEqual(object_label(obj), 'Spotify · API key · Dezolent')

    def test_observation_names_field_and_recording(self):
        target = EntityTarget(organization=Organization(name='Bounced Studio'), recording=Recording(title='Tell Me'))
        self.assertEqual(object_label(target), 'Tell Me')
        self.assertEqual(object_label(FieldObservation(target=target, field_name='title')), 'Title · Tell Me')
