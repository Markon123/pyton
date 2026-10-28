from datetime import date
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Consent, MinistryAuthority, Profile, ProvisionedAccount


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountHierarchyTests(TestCase):
    initial_password = 'Initial!M1nistry726Strong'
    new_password = 'Personal!N3wOwner4639'

    def ministry(self, active=True):
        with patch.dict(os.environ, {'FSP_MINISTRY_PASSWORD': self.initial_password}):
            call_command('bootstrap_ministry', stdout=StringIO())
        user = get_user_model().objects.get(username='ministry')
        if active:
            ProvisionedAccount.objects.filter(user=user).update(must_change_password=False, activated_at=timezone.now())
        return user

    def federation_payload(self, username='regional-office'):
        return {'institution': 'Региональная федерация', 'username': username,
                'email': username + '@example.test', 'password1': 'Temporary!Sp0rt726Alpha',
                'password2': 'Temporary!Sp0rt726Alpha', 'roles': ['ministry'], 'is_superuser': True}

    def test_clean_setup_precreates_only_ministry_and_never_resets_password(self):
        call_command('prepare_project', stdout=StringIO())
        output = StringIO()
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'FSP_MINISTRY_PASSWORD': ''}):
            access_file = Path(folder) / 'first-access.txt'
            call_command('bootstrap_ministry', credentials_file=str(access_file), stdout=output)
            user = get_user_model().objects.get()
            self.assertEqual(user.username, 'ministry')
            self.assertEqual(user.profile.roles, ['ministry'])
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)
            secret = access_file.read_text().split('Временный пароль: ', 1)[1].splitlines()[0]
            self.assertTrue(user.check_password(secret))
            self.assertNotIn(secret, output.getvalue())
            if os.name != 'nt':
                self.assertEqual(access_file.stat().st_mode & 0o777, 0o600)
            old_hash = user.password
            call_command('bootstrap_ministry', credentials_file=str(access_file), stdout=output)
            user.refresh_from_db()
            self.assertEqual(user.password, old_hash)
            self.assertEqual(get_user_model().objects.count(), 1)
            self.assertFalse(Consent.objects.exists())

    def test_existing_named_account_is_not_silently_promoted_or_overwritten(self):
        user = get_user_model().objects.create_user('ministry', password='Existing!NoReset927')
        Profile.objects.create(user=user, roles=['athlete'])
        with patch.dict(os.environ, {'FSP_MINISTRY_PASSWORD': self.initial_password}):
            with self.assertRaises(CommandError):
                call_command('bootstrap_ministry')
        user.refresh_from_db()
        self.assertTrue(user.check_password('Existing!NoReset927'))
        self.assertEqual(user.profile.roles, ['athlete'])
        self.assertFalse(MinistryAuthority.objects.exists())

    def test_single_principal_is_enforced_in_database(self):
        ministry = self.ministry()
        other = get_user_model().objects.create_user('another-chief')
        with self.assertRaises(IntegrityError), transaction.atomic():
            MinistryAuthority.objects.create(pk=2, user=other)
        self.assertEqual(MinistryAuthority.objects.get().user, ministry)

    def test_first_login_requires_password_change_and_actual_consent(self):
        ministry = self.ministry(active=False)
        self.client.force_login(ministry)
        self.assertRedirects(self.client.get(reverse('federation_accounts')), reverse('account_activation'))
        response = self.client.post(reverse('account_activation'), {'new_password1': self.new_password, 'new_password2': self.new_password})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Consent.objects.exists())
        response = self.client.post(reverse('account_activation'), {'new_password1': self.new_password, 'new_password2': self.new_password, 'personal_consent': True})
        self.assertEqual(response.status_code, 302)
        ministry.refresh_from_db()
        self.assertTrue(ministry.check_password(self.new_password))
        self.assertFalse(ministry.check_password(self.initial_password))
        self.assertFalse(ministry.provisioning.must_change_password)
        self.assertTrue(Consent.objects.filter(user=ministry, kind='personal_data').exists())
        self.assertEqual(self.client.get(reverse('federation_accounts')).status_code, 200)

    def test_only_principal_creates_federation_and_posted_privileges_are_ignored(self):
        ministry = self.ministry()
        self.client.force_login(ministry)
        response = self.client.post(reverse('federation_accounts'), self.federation_payload())
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='regional-office')
        self.assertEqual(user.profile.roles, ['federation'])
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(user.provisioning.created_by, ministry)
        self.assertTrue(user.provisioning.must_change_password)
        self.assertFalse(Consent.objects.filter(user=user).exists())
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse('competitions')), reverse('account_activation'))
        user.provisioning.must_change_password = False
        user.provisioning.save()
        self.assertEqual(self.client.post(reverse('federation_accounts'), self.federation_payload('illegal')).status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username='illegal').exists())

    def test_ministry_role_alone_does_not_create_second_chief(self):
        self.ministry()
        user = get_user_model().objects.create_user('forged-ministry')
        Profile.objects.create(user=user, roles=['ministry'])
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('federation_accounts'), self.federation_payload('illegal')).status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username='illegal').exists())

    def test_public_registration_rejects_federation_and_ministry_roles(self):
        call_command('prepare_project', stdout=StringIO())
        from .models import Discipline
        base = {'username': 'self-promoted', 'first_name': 'Тест', 'last_name': 'Проверочный',
                'email': 'self@example.test', 'birth_date': date(timezone.localdate().year - 20, 1, 1).isoformat(),
                'municipality': 'Оренбург', 'start_year': timezone.localdate().year,
                'disciplines': [Discipline.objects.first().pk], 'personal_consent': True,
                'password1': 'Secure!Registrati0n927Alpha', 'password2': 'Secure!Registrati0n927Alpha'}
        for role in ('federation', 'ministry'):
            with self.subTest(role=role):
                self.assertEqual(self.client.post(reverse('register'), {**base, 'roles': [role]}).status_code, 400)
                api = APIClient()
                self.assertEqual(api.post(reverse('api:register'), {**base, 'roles': [role]}, format='json').status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username='self-promoted').exists())

    def test_federation_cannot_promote_itself_or_change_principal_via_api(self):
        ministry = self.ministry()
        fed = get_user_model().objects.create_user('normal-federation')
        Profile.objects.create(user=fed, roles=['federation'])
        api = APIClient()
        api.force_authenticate(user=fed, token={'active_role': 'federation'})
        for user in (fed, ministry):
            response = api.patch(reverse('api:athlete', args=[user.profile.pk]), {'roles': ['ministry']}, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Profile.objects.get(user=fed).roles, ['federation'])
        self.assertEqual(Profile.objects.get(user=ministry).roles, ['ministry'])

    def test_pending_federation_cannot_use_jwt_to_skip_first_login(self):
        ministry = self.ministry()
        self.client.force_login(ministry)
        self.client.post(reverse('federation_accounts'), self.federation_payload())
        api = APIClient()
        response = api.post(reverse('api:login'), {'username': 'regional-office', 'password': 'Temporary!Sp0rt726Alpha'}, format='json')
        self.assertEqual(response.status_code, 200)
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(api.get(reverse('api:me')).status_code, 403)

    def test_principal_cannot_be_disabled_through_federation_endpoint(self):
        ministry = self.ministry()
        self.client.force_login(ministry)
        response = self.client.post(reverse('federation_account_status', args=[ministry.provisioning.pk]), {'status': 'inactive'})
        self.assertEqual(response.status_code, 404)
        ministry.refresh_from_db()
        self.assertTrue(ministry.is_active)

    def test_login_page_has_no_demo_accounts_or_embedded_password(self):
        response = self.client.get(reverse('login'))
        for text in ('data-demo', 'FspDemo2026!', 'Познакомьтесь с платформой', 'демоаккаунт', 'Демонстрационная версия'):
            self.assertNotContains(response, text)
        self.assertContains(response, 'Создать аккаунт')

    def test_ministry_resets_federation_password_and_old_jwt_cannot_survive(self):
        ministry = self.ministry()
        self.client.force_login(ministry)
        self.client.post(reverse('federation_accounts'), self.federation_payload())
        user = get_user_model().objects.get(username='regional-office')
        ProvisionedAccount.objects.filter(user=user).update(must_change_password=False, activated_at=timezone.now())
        api = APIClient()
        login = api.post(reverse('api:login'), {'username': user.username, 'password': 'Temporary!Sp0rt726Alpha'}, format='json')
        self.assertEqual(login.status_code, 200)
        old_access = login.data['access']
        api.credentials(HTTP_AUTHORIZATION='Bearer '+old_access)
        self.assertEqual(api.get(reverse('api:me')).status_code, 200)
        reset = self.client.post(reverse('federation_account_reset', args=[user.provisioning.pk]),
            {'new_password1': 'New!Temporary742Beta', 'new_password2': 'New!Temporary742Beta'})
        self.assertEqual(reset.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password('New!Temporary742Beta'))
        self.assertTrue(ProvisionedAccount.objects.get(user=user).must_change_password)
        self.assertEqual(api.get(reverse('api:me')).status_code, 401)
        fresh = APIClient().post(reverse('api:login'), {'username': user.username, 'password': 'New!Temporary742Beta'}, format='json')
        self.assertEqual(fresh.status_code, 200)
        api.credentials(HTTP_AUTHORIZATION='Bearer '+fresh.data['access'])
        self.assertEqual(api.get(reverse('api:me')).status_code, 403)

    def test_principal_can_disable_federation_without_modifying_sporting_roles(self):
        ministry = self.ministry()
        self.client.force_login(ministry)
        self.client.post(reverse('federation_accounts'), self.federation_payload())
        account = ProvisionedAccount.objects.get(kind='federation')
        response = self.client.post(reverse('federation_account_status', args=[account.pk]), {'status': 'inactive'})
        self.assertEqual(response.status_code, 302)
        account.user.refresh_from_db()
        self.assertFalse(account.user.is_active)
        self.assertEqual(account.user.profile.roles, ['federation'])
        self.assertEqual(APIClient().post(reverse('api:login'), {'username': account.user.username, 'password': 'Temporary!Sp0rt726Alpha'}, format='json').status_code, 401)
