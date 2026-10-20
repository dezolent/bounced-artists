from unittest.mock import patch
from django.db import OperationalError
from django.test import TestCase, override_settings


class ProbeTests(TestCase):
    def test_liveness_does_not_query_database(self):
        with self.assertNumQueries(0):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_uses_database_and_disables_caching(self):
        with self.assertNumQueries(1):
            response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertIn("no-store", response["Cache-Control"])

    def test_readiness_failure_contains_no_database_details(self):
        with patch("core.views.connection.cursor", side_effect=OperationalError("sensitive-database-detail")):
            response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn("sensitive", response.content.decode())

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^health/$", r"^ready/$"])
    def test_probe_exemption_does_not_disable_https_for_admin(self):
        self.assertEqual(self.client.get("/health/").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 301)

    def test_unrecognized_host_is_rejected(self):
        self.assertEqual(self.client.get("/health/", HTTP_HOST="attacker.invalid").status_code, 400)
