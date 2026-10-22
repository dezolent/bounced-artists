"""Administrator account creation and workspace access are one audited operation."""
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client,TestCase
from django.urls import reverse
from unittest.mock import patch

from artists.models import Artist,ArtistMembership,ArtistResourceGrant,Organization,OrganizationMembership
from catalog.models import Recording
from provenance.models import AuditEvent


class UserManagementTests(TestCase):
    def setUp(self):
        self.org=Organization.objects.get(slug='bounced-studio')
        self.first=Artist.objects.create(organization=self.org,name='First workspace',slug='account-first',management_status='managed')
        self.second=Artist.objects.create(organization=self.org,name='Second workspace',slug='account-second',management_status='managed')
        self.collaborator=Artist.objects.create(organization=self.org,name='Collaborator only',slug='account-collaborator')
        self.admin=get_user_model().objects.create_superuser(username='account-admin',password=None)
        self.target=get_user_model().objects.create_user(username='existing-artist',email='old@example.test')
        self.client.force_login(self.admin)
        self.list_url='/app/users/'
        self.create_url='/app/users/new/'
        self.edit_url=f'/app/users/{self.target.pk}/edit/'

    def data(self,url=None,**updates):
        response=self.client.get(url or self.create_url)
        self.assertEqual(response.status_code,200)
        data={field.html_name:field.value() if field.value() is not None else '' for key in ('account_form','workspace_form') for field in response.context[key]}
        if url is None or url==self.create_url:
            data.update(username='new-artist',email='new@example.test',first_name='New',last_name='Artist',password1='Cobalt@River-74-Account',password2='Cobalt@River-74-Account')
        data.update(updates)
        return data

    def test_only_superusers_can_manage_accounts_not_artist_owners_or_staff(self):
        for superuser,staff,role in [(False,False,'owner'),(False,True,'owner')]:
            user=get_user_model().objects.create_user(username=f'limited-{staff}',is_staff=staff,is_superuser=superuser)
            ArtistMembership.objects.create(artist=self.first,user=user,role=role)
            self.client.force_login(user)
            for url in (self.list_url,self.create_url,self.edit_url):
                with self.subTest(user=user.username,url=url):
                    self.assertEqual(self.client.get(url).status_code,403)
                    self.assertEqual(self.client.post(url,{}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.list_url).status_code,302)

    def test_admin_navigation_is_available_without_a_selected_workspace(self):
        self.assertContains(self.client.get(self.list_url),'Add user')
        response=self.client.get('/app/')
        self.assertContains(response,self.list_url)
        self.client.force_login(self.target)
        self.assertNotContains(self.client.get('/app/'),'href="/app/users/"')

    def test_create_account_hashes_password_and_assigns_roles_without_other_grants(self):
        data=self.data(**{f'workspace_{self.first.pk}':'owner',f'workspace_{self.second.pk}':'viewer'})
        response=self.client.post(self.create_url,data)
        self.assertRedirects(response,self.list_url,fetch_redirect_response=False)
        landing=self.client.get(response.url)
        self.assertContains(landing,'User created and workspace access saved.')
        self.assertContains(landing,'new-artist')
        account=get_user_model().objects.get(username='new-artist')
        self.assertTrue(account.check_password('Cobalt@River-74-Account'))
        self.assertFalse(account.is_staff or account.is_superuser)
        self.assertEqual(dict(account.artist_memberships.values_list('artist_id','role')),{self.first.pk:'owner',self.second.pk:'viewer'})
        self.assertFalse(OrganizationMembership.objects.filter(user=account).exists())
        self.assertFalse(ArtistResourceGrant.objects.exists())
        self.assertTrue(LogEntry.objects.filter(user=self.admin,object_id=str(account.pk)).exists())
        self.assertEqual(AuditEvent.objects.filter(action='portal.workspace_access').count(),2)
        messages=' '.join(LogEntry.objects.values_list('change_message',flat=True))
        self.assertNotIn('Cobalt@River-74-Account',messages)
        self.client.logout()
        self.assertTrue(self.client.login(username='new-artist',password='Cobalt@River-74-Account'))
        self.assertEqual(self.client.get(reverse('portal:catalog',args=[self.first.pk])).status_code,200)

    def test_edit_changes_and_removes_roles_preserving_hidden_historical_memberships(self):
        first=ArtistMembership.objects.create(user=self.target,artist=self.first,role='viewer')
        historical=ArtistMembership.objects.create(user=self.target,artist=self.collaborator,role='owner')
        data=self.data(self.edit_url,**{f'workspace_{self.first.pk}':'editor',f'workspace_{self.second.pk}':'viewer'})
        self.assertEqual(self.client.post(self.edit_url,data).status_code,302)
        first.refresh_from_db();historical.refresh_from_db()
        self.assertEqual(first.role,'editor')
        self.assertEqual(historical.role,'owner')
        data=self.data(self.edit_url,**{f'workspace_{self.first.pk}':'none'})
        self.assertEqual(self.client.post(self.edit_url,data).status_code,302)
        self.assertFalse(ArtistMembership.objects.filter(pk=first.pk).exists())
        self.client.force_login(self.target)
        self.assertEqual(self.client.get(reverse('portal:catalog',args=[self.first.pk])).status_code,404)

    def test_weak_password_duplicate_username_and_invalid_role_do_not_create_accounts(self):
        for changes in ({'password1':'short','password2':'short'},{'username':'EXISTING-ARTIST'},{f'workspace_{self.first.pk}':'admin'}):
            response=self.client.post(self.create_url,self.data(**changes))
            self.assertEqual(response.status_code,400)
            self.assertFalse(get_user_model().objects.filter(username='new-artist').exists())
        self.assertEqual(ArtistMembership.objects.count(),0)

    def test_forged_privilege_fields_and_nonworkspace_assignments_are_rejected(self):
        for extra in ({'is_staff':'on','is_superuser':'on'},{f'workspace_{self.collaborator.pk}':'owner'},{'groups':'1','user_permissions':'1'}):
            self.assertEqual(self.client.post(self.create_url,self.data(**extra)).status_code,400)
            self.assertFalse(get_user_model().objects.filter(username='new-artist').exists())

    def test_missing_workspace_field_cannot_silently_revoke_access(self):
        ArtistMembership.objects.create(user=self.target,artist=self.first,role='editor')
        data=self.data(self.edit_url)
        data.pop(f'workspace_{self.first.pk}')
        self.assertEqual(self.client.post(self.edit_url,data).status_code,400)
        self.assertEqual(ArtistMembership.objects.get(user=self.target,artist=self.first).role,'editor')

    def test_stale_membership_or_profile_edit_is_rejected(self):
        membership=ArtistMembership.objects.create(user=self.target,artist=self.first,role='viewer')
        data=self.data(self.edit_url,first_name='Stale')
        membership.role='owner';membership.save()
        self.assertEqual(self.client.post(self.edit_url,data).status_code,409)
        self.target.refresh_from_db()
        self.assertEqual(self.target.first_name,'')
        self.assertEqual(ArtistMembership.objects.get(pk=membership.pk).role,'owner')
        data=self.data(self.edit_url,first_name='Stale')
        get_user_model().objects.filter(pk=self.target.pk).update(email='concurrent@example.test')
        self.assertEqual(self.client.post(self.edit_url,data).status_code,409)

    def test_privileged_accounts_cannot_be_edited_through_artist_account_form(self):
        self.assertEqual(self.client.get(f'/app/users/{self.admin.pk}/edit/').status_code,404)
        self.assertEqual(self.client.post(f'/app/users/{self.admin.pk}/edit/',{}).status_code,404)

    def test_failed_membership_write_rolls_back_new_account_and_audit(self):
        data=self.data(**{f'workspace_{self.first.pk}':'owner'})
        with patch.object(ArtistMembership,'save',side_effect=IntegrityError('Simulated membership failure')):
            response=self.client.post(self.create_url,data)
        self.assertEqual(response.status_code,400)
        self.assertFalse(get_user_model().objects.filter(username='new-artist').exists())
        self.assertFalse(LogEntry.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action='portal.workspace_access').exists())

    def test_posts_require_csrf_and_passwords_are_not_echoed_on_validation_failure(self):
        csrf_client=Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.create_url,{}).status_code,403)
        data=self.data(password2='Different@River-74-Account')
        response=self.client.post(self.create_url,data)
        self.assertEqual(response.status_code,400)
        self.assertNotContains(response,'value="Cobalt@River-74-Account"',status_code=400)
        self.assertNotContains(response,'value="Different@River-74-Account"',status_code=400)

    def test_user_list_searches_names_and_shows_readable_workspace_roles(self):
        ArtistMembership.objects.create(user=self.target,artist=self.first,role='viewer')
        response=self.client.get(self.list_url,{'q':'existing'})
        self.assertContains(response,'existing-artist')
        self.assertContains(response,'First workspace')
        self.assertContains(response,'Viewer')
        self.assertEqual([row.pk for row in response.context['accounts']],[self.target.pk])
