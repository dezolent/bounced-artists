"""System privileges and passwords have distinct, administrator-only forms."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from artists.models import Artist, ArtistMembership, ArtistResourceGrant, Organization, OrganizationMembership
from portal.system_accounts import TOKEN_SALT, _form, save_system_account


User = get_user_model()


class SystemAccountTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='system-admin', password=None)
        self.target = User.objects.create_user(username='system-artist', password=None)
        self.client.force_login(self.admin)
        self.access_url = reverse('portal:system-account', args=[self.target.pk])
        self.password_url = reverse('portal:reset-account-password', args=[self.target.pk])

    def data(self, url=None, **updates):
        response = self.client.get(url or self.access_url)
        self.assertEqual(response.status_code, 200)
        result = {field.html_name: field.value() if field.value() is not None else '' for field in response.context['form']}
        result.update(updates)
        return result

    def password_data(self, **updates):
        return self.data(self.password_url, new_password1='Waterfall@Orange-742', new_password2='Waterfall@Orange-742', **updates)

    def test_nonstaff_staff_and_nonstaff_superuser_are_denied(self):
        for staff, superuser in ((False, False), (True, False), (False, True)):
            user = User.objects.create_user(username=f'limited-{staff}-{superuser}', is_staff=staff, is_superuser=superuser)
            self.client.force_login(user)
            for url in (self.access_url, self.password_url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.access_url).status_code, 302)

    def test_roles_and_active_flag_use_explicit_pairs(self):
        for role, flags in (('staff', (True, False)), ('administrator', (True, True)), ('artist', (False, False))):
            response = self.client.post(self.access_url, self.data(role=role, is_active='on'))
            self.assertEqual(response.status_code, 302)
            self.target.refresh_from_db()
            self.assertEqual((self.target.is_staff, self.target.is_superuser), flags)
        data = self.data()
        data.pop('is_active')
        self.assertEqual(self.client.post(self.access_url, data).status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.target.pk)).count(), 4)

    def test_promotion_explains_global_access_and_preserves_memberships(self):
        org = Organization.objects.get(slug='bounced-studio')
        artist = Artist.objects.create(organization=org, name='Privileges artist', slug='privileges-artist', management_status='managed')
        member = ArtistMembership.objects.create(user=self.target, artist=artist, role='viewer')
        self.assertContains(self.client.get(self.access_url), 'internal global access beyond those assignments')
        self.assertEqual(self.client.post(self.access_url, self.data(role='staff')).status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.role, 'viewer')
        self.assertEqual(ArtistMembership.objects.filter(user=self.target).count(), 1)
        self.assertFalse(OrganizationMembership.objects.filter(user=self.target).exists())
        self.assertFalse(ArtistResourceGrant.objects.exists())

    def test_invalid_role_and_unexpected_fields_cannot_change_privileges(self):
        for updates in ({'role': 'root'}, {'is_superuser': 'on'}, {'password': 'unsafe'}, {'user_permissions': '1'}, {'groups': '1'}):
            self.assertEqual(self.client.post(self.access_url, self.data(**updates)).status_code, 400)
            self.target.refresh_from_db()
            self.assertFalse(self.target.is_staff or self.target.is_superuser)
            self.assertEqual(self.target.username, 'system-artist')
            self.assertFalse(self.target.has_usable_password())

    def test_privileged_account_profile_edits_and_case_insensitive_username_validation(self):
        self.target.is_staff = self.target.is_superuser = True
        self.target.save(update_fields=['is_staff', 'is_superuser'])
        response = self.client.post(self.access_url, self.data(username='renamed-admin', first_name='Portal', last_name='Operator', email='operator@example.test'))
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual((self.target.username, self.target.first_name, self.target.last_name, self.target.email), ('renamed-admin', 'Portal', 'Operator', 'operator@example.test'))
        self.assertTrue(self.target.is_staff and self.target.is_superuser)
        for updates in ({'username': 'SYSTEM-ADMIN'}, {'username': 'invalid/name'}, {'email': 'invalid'}):
            self.assertEqual(self.client.post(self.access_url, self.data(**updates)).status_code, 400)
        self.target.refresh_from_db()
        self.assertEqual(self.target.username, 'renamed-admin')

    def test_snapshot_rejects_target_changes_tampering_and_cross_action(self):
        data = self.data(role='staff')
        self.target.set_password('Concurrent@Password-748')
        self.target.save(update_fields=['password'])
        self.assertEqual(self.client.post(self.access_url, data).status_code, 409)
        data = self.data(role='staff', version='tampered')
        self.assertEqual(self.client.post(self.access_url, data).status_code, 409)
        data = self.password_data()
        data['version'] = self.data()['version']
        self.assertEqual(self.client.post(self.password_url, data).status_code, 409)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_staff)
        self.assertTrue(self.target.check_password('Concurrent@Password-748'))

    def test_snapshot_contains_only_credential_digest_and_binds_actor_target(self):
        data = self.data()
        token = signing.loads(data['version'], salt=TOKEN_SALT)
        self.assertEqual(token['actor'], self.admin.pk)
        self.assertEqual(token['target'], self.target.pk)
        self.assertNotIn(self.target.password, str(token))
        other = User.objects.create_user(username='system-other')
        self.assertEqual(self.client.post(reverse('portal:system-account', args=[other.pk]), data).status_code, 409)
        self.admin.first_name = 'Changed'
        self.admin.save(update_fields=['first_name'])
        self.assertEqual(self.client.post(self.access_url, data).status_code, 409)

    def test_final_administrator_cannot_be_demoted_or_disabled(self):
        url = reverse('portal:system-account', args=[self.admin.pk])
        for changes in ({'role': 'artist'}, {'role': 'staff'}, {'is_active': ''}):
            response = self.client.post(url, self.data(url, **changes))
            self.assertContains(response, 'final active administrator', status_code=400)
            self.admin.refresh_from_db()
            self.assertTrue(self.admin.is_active and self.admin.is_staff and self.admin.is_superuser)

    def test_self_demotion_requires_another_administrator_to_act(self):
        User.objects.create_superuser(username='other-system-admin', password=None)
        url = reverse('portal:system-account', args=[self.admin.pk])
        response = self.client.post(url, self.data(url, role='staff'))
        self.assertContains(response, 'own administrator account', status_code=400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)

    def test_administrator_can_demote_another_administrator(self):
        self.target.is_staff = self.target.is_superuser = True
        self.target.save(update_fields=['is_staff', 'is_superuser'])
        self.assertEqual(self.client.post(self.access_url, self.data(role='artist')).status_code, 302)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_staff or self.target.is_superuser)

    def test_current_actor_permission_is_rechecked_inside_transaction(self):
        data = self.data(role='staff')
        User.objects.filter(pk=self.admin.pk).update(is_superuser=False)
        with self.assertRaises(PermissionDenied):
            save_system_account(self.admin, self.target.pk, 'access', data)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_staff)

    def test_password_reset_hashes_audits_and_does_not_assign_workspaces(self):
        data = self.password_data()
        response = self.client.post(self.password_url, data)
        self.assertRedirects(response, reverse('portal:user-list'), fetch_redirect_response=False)
        self.target.refresh_from_db()
        self.assertTrue(self.target.check_password(data['new_password1']))
        self.assertNotEqual(self.target.password, data['new_password1'])
        self.assertFalse(self.target.is_staff or self.target.is_superuser)
        self.assertFalse(ArtistMembership.objects.filter(user=self.target).exists())
        self.assertFalse(OrganizationMembership.objects.filter(user=self.target).exists())
        self.assertFalse(ArtistResourceGrant.objects.exists())
        audit = LogEntry.objects.get(object_id=str(self.target.pk))
        self.assertEqual(audit.change_message, 'Reset account password through the portal.')
        self.assertNotIn(data['new_password1'], audit.change_message)
        self.assertNotIn(self.target.password, audit.change_message)
        self.assertContains(self.client.get(response.url), 'no email was sent')

    def test_password_validation_and_forged_fields_leave_account_unchanged(self):
        for changes in ({'new_password1': 'short', 'new_password2': 'short'},
                        {'new_password1': '123456789012', 'new_password2': '123456789012'},
                        {'new_password1': 'password1234', 'new_password2': 'password1234'},
                        {'new_password2': 'Different@Password-742'}, {'role': 'administrator'}, {'is_staff': 'on'}):
            data = self.password_data()
            data.update(changes)
            response = self.client.post(self.password_url, data)
            self.assertEqual(response.status_code, 400)
            self.assertNotContains(response, f'value="{data["new_password1"]}"', status_code=400)
            self.assertNotContains(response, f'value="{data["new_password2"]}"', status_code=400)
            self.target.refresh_from_db()
            self.assertFalse(self.target.has_usable_password())
            self.assertFalse(self.target.is_staff)
        self.assertFalse(LogEntry.objects.exists())

    def test_password_stale_snapshot_and_self_reset_preserve_current_session(self):
        data = self.password_data()
        User.objects.filter(pk=self.target.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.password_url, data).status_code, 409)
        url = reverse('portal:reset-account-password', args=[self.admin.pk])
        data = self.data(url, new_password1='Saffron@Riverside-926', new_password2='Saffron@Riverside-926')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.get(reverse('portal:user-list')).status_code, 200)

    def test_csrf_and_user_list_actions(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for url in (self.access_url, self.password_url):
            self.assertEqual(client.post(url, {}).status_code, 403)
        response = self.client.get(reverse('portal:user-list'))
        self.assertContains(response, self.access_url)
        self.assertContains(response, self.password_url)
        self.assertContains(response, reverse('portal:user-edit', args=[self.target.pk]))
        self.assertNotContains(response, 'Django Admin')


class ConcurrentAdministratorTests(TransactionTestCase):
    def test_cross_demotions_cannot_remove_both_administrators(self):
        first = User.objects.create_superuser(username='parallel-first', password=None)
        second = User.objects.create_superuser(username='parallel-second', password=None)
        barrier = Barrier(2)

        def demote(actor, target):
            close_old_connections()
            form = _form(actor, target, 'access')
            data = {**form.initial, 'role': 'artist', 'is_active': 'on'}
            barrier.wait(timeout=10)
            try:
                saved, _ = save_system_account(actor, target.pk, 'access', data)
                return bool(saved)
            except PermissionDenied:
                return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(demote, first, second), pool.submit(demote, second, first)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(User.objects.filter(is_active=True, is_staff=True, is_superuser=True).count(), 1)
