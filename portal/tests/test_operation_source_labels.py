from dataclasses import replace
from types import SimpleNamespace

from django.test import SimpleTestCase

from analytics.models import MetricSeries
from provenance.models import SourceRecord
from portal.operation_registry import REGISTRY
from portal.operations import display_fields


class OperationSourceLabelTests(SimpleTestCase):
    def test_collection_backend_names_have_friendly_display_without_changing_evidence(self):
        for obj, key, name, label in ((SourceRecord(collector='omkar'), 'provenance-sourcerecord', 'collector', 'Collector'),
                (MetricSeries(source_method='omkar'), 'analytics-metricseries', 'source_method', 'Source method')):
            with self.subTest(field=name):
                fields = display_fields(obj, replace(REGISTRY[key], fields=(name,)), SimpleNamespace(is_superuser=True))
                value = next(row['value'] for row in fields if row['name'] == label)
                self.assertEqual(value, 'Platform collection')
                self.assertEqual(getattr(obj, name), 'omkar')

    def test_other_collection_names_remain_identifiable(self):
        obj = SourceRecord(collector='spotify-web-api')
        fields = display_fields(obj, replace(REGISTRY['provenance-sourcerecord'], fields=('collector',)), SimpleNamespace(is_superuser=True))
        self.assertEqual(next(row['value'] for row in fields if row['name']=='Collector'), 'spotify-web-api')
