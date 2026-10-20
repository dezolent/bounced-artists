from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient
from artists.models import Artist, ArtistContact, ArtistMembership, Organization


class AccessTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.organization, name="Dezolent", slug="dezolent")
        self.other = Artist.objects.create(organization=self.organization, name="SRY4U", slug="sry4u")
        self.user = get_user_model().objects.create_user(username="member", password="test-only-password")
        self.api = APIClient()

    def test_anonymous_api_denied(self):
        for url in ("/api/v1/artists/", f"/api/v1/artists/{self.artist.pk}/"):
            self.assertEqual(self.api.get(url).status_code, 403)

    def test_each_role_sees_only_its_artist_and_other_id_is_404(self):
        membership = ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        self.api.force_authenticate(self.user)
        for role in ("owner", "editor", "viewer"):
            with self.subTest(role=role):
                membership.role = role
                membership.save()
                result = self.api.get("/api/v1/artists/")
                self.assertEqual(result.status_code, 200)
                self.assertEqual([a["slug"] for a in result.json()["results"]], ["dezolent"])
                self.assertEqual(self.api.get(f"/api/v1/artists/{self.artist.pk}/").status_code, 200)
                self.assertEqual(self.api.get(f"/api/v1/artists/{self.other.pk}/").status_code, 404)

    def test_model_permission_does_not_grant_cross_artist_access(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_artist"))
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.get("/api/v1/artists/").json()["count"], 0)
        self.assertEqual(self.api.get(f"/api/v1/artists/{self.other.pk}/").status_code, 404)

    def test_contacts_are_not_serialized_and_api_writes_are_not_enabled(self):
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role="owner")
        ArtistContact.objects.create(artist=self.artist, value="private@example.com", purpose="booking")
        self.api.force_authenticate(self.user)
        response = self.api.get(f"/api/v1/artists/{self.artist.pk}/")
        self.assertNotIn("private@example.com", response.content.decode())
        for method in ("post", "put", "patch", "delete"):
            self.assertEqual(getattr(self.api, method)(f"/api/v1/artists/{self.artist.pk}/", {}, format="json").status_code, 405)

    def test_staff_has_global_api_visibility(self):
        self.user.is_staff = True
        self.user.save()
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.get("/api/v1/artists/").json()["count"], 2)

    def test_inactive_user_is_denied(self):
        self.user.is_active = False
        self.user.save()
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.get("/api/v1/artists/").status_code, 403)

    def test_membership_revocation_takes_effect(self):
        membership = ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        self.api.force_authenticate(self.user)
        self.assertEqual(self.api.get(f"/api/v1/artists/{self.artist.pk}/").status_code, 200)
        membership.delete()
        self.assertEqual(self.api.get(f"/api/v1/artists/{self.artist.pk}/").status_code, 404)

    def test_nonstaff_cannot_access_admin_even_with_model_permissions(self):
        self.user.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertEqual(self.client.get("/admin/artists/artist/").status_code, 302)

    def test_staff_cannot_escalate_user_or_membership_permissions(self):
        self.user.is_staff = True
        self.user.save()
        self.user.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/admin/core/user/").status_code, 403)
        self.assertEqual(self.client.get("/admin/artists/artistmembership/").status_code, 403)
        response = self.client.post(f"/admin/core/user/{self.user.pk}/change/", {"is_superuser": "on"})
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.client.get("/admin/artists/artist/").status_code, 200)

    def test_superuser_can_manage_artists_memberships_and_users(self):
        admin = get_user_model().objects.create_superuser(username="administrator", password="test-only-password")
        self.client.force_login(admin)
        for url in ("/admin/", "/admin/artists/artist/", "/admin/artists/artistmembership/", "/admin/core/user/"):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_admin_login_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.post("/admin/login/", {"username": "member", "password": "test-only-password"})
        self.assertEqual(response.status_code, 403)
