from django.test import TestCase
from django.contrib.auth import get_user_model


class PortalEntryTests(TestCase):
    def test_root_is_artist_login_and_keeps_private_catalog_out(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertNotContains(response, 'Django administration')

    def test_authenticated_root_opens_workspace(self):
        user = get_user_model().objects.create_user(username='artist', password='test-password')
        self.client.force_login(user)
        self.assertRedirects(self.client.get('/'), '/app/', fetch_redirect_response=False)

    def test_private_workspace_requires_login(self):
        self.assertRedirects(self.client.get('/app/'), '/?next=/app/', fetch_redirect_response=False)

    def test_login_rejects_external_next_and_logout_is_post_only(self):
        get_user_model().objects.create_user(username='artist', password='test-password')
        response = self.client.post('/?next=https://evil.example/', {'username': 'artist', 'password': 'test-password'})
        self.assertRedirects(response, '/app/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/logout/').status_code, 405)
