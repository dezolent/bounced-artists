import os
import subprocess
import sys
import unittest


class ConfigurationTests(unittest.TestCase):
    def load_settings(self, module, **overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DATABASE_", "PG"))}
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", f"from django.conf import settings; settings.configure(default_settings=__import__('{module}', fromlist=[''])); assert settings.SECRET_KEY"],
            env=env, capture_output=True, text=True,
        )

    def production(self, **overrides):
        env = {
            "DJANGO_SECRET_KEY": "test-only-long-secret-" * 5,
            "DATABASE_URL": "postgresql://dev:dev@postgres.railway.internal:5432/railway",
            "DJANGO_ALLOWED_HOSTS": "backend.example.com,healthcheck.railway.app",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://backend.example.com",
        }
        env.update(overrides)
        return self.load_settings("config.settings.production", **env)

    def test_complete_production_configuration_loads(self):
        result = self.production()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_production_configuration_fails_clearly(self):
        for name in ("DJANGO_SECRET_KEY", "DATABASE_URL", "DJANGO_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS"):
            with self.subTest(name=name):
                result = self.production(**{name: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_production_rejects_unsafe_values_without_printing_secret(self):
        for values in (
            {"DJANGO_SECRET_KEY": "short-secret"},
            {"DJANGO_ALLOWED_HOSTS": "*"},
            {"DJANGO_ALLOWED_HOSTS": ".example.com"},
            {"DJANGO_CSRF_TRUSTED_ORIGINS": "http://backend.example.com"},
            {"DATABASE_URL": "sqlite:///db.sqlite3"},
        ):
            with self.subTest(keys=list(values)):
                result = self.production(**values)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("short-secret", result.stderr)

    def test_local_settings_reject_remote_database(self):
        result = self.load_settings("config.settings.local", DATABASE_URL="postgresql://dev:dev@production.example.com/railway")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)

    def test_local_settings_allow_separate_loopback_database(self):
        result = self.load_settings("config.settings.local", DATABASE_URL="postgresql://dev:dev@127.0.0.1:55432/bounced_artists_dev")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blank_local_secret_uses_development_fallback(self):
        result = self.load_settings("config.settings.local", DJANGO_SECRET_KEY="")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_database_options_cannot_override_loopback(self):
        for query in ("host=production.example.com", "hostaddr=203.0.113.10", "service=production"):
            result = self.load_settings("config.settings.local", DATABASE_URL=f"postgresql://dev:dev@127.0.0.1/dev?{query}")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("loopback", result.stderr)

    def test_local_libpq_environment_cannot_override_destination(self):
        for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
            with self.subTest(key=key):
                result = self.load_settings("config.settings.local", **{key: "remote-override"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("loopback", result.stderr)
