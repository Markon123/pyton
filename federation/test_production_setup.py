from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase

from federation.models import Consent, Discipline, Profile, RatingRule
from scripts.configure_deployment import create_config


class ProductionConfigTests(SimpleTestCase):
    def test_private_config_is_complete_and_does_not_overwrite(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '.env.production'
            create_config('Platform.Example.org', path)
            text = path.read_text()
            self.assertIn('PUBLIC_BASE_URL=https://platform.example.org', text)
            self.assertIn('FSP_SEED_DEMO=0', text)
            self.assertIn('DJANGO_DEBUG=0', text)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                create_config('other.example.org', path)
            self.assertEqual(path.read_text(), text)

    def test_domain_validation_blocks_env_injection_and_ip_only(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '.env.production'
            for host in ('127.0.0.1', 'https://example.org', 'domain.org\nDJANGO_DEBUG=1', 'example.org/path', 'domain.org:8000'):
                with self.assertRaises(ValueError):
                    create_config(host, path)
            self.assertFalse(path.exists())


class FederationBootstrapTests(TestCase):
    def test_clean_production_has_six_disciplines_and_editable_rating_without_demo_people(self):
        output = StringIO()
        call_command('prepare_project', stdout=output)
        self.assertEqual(Discipline.objects.count(), 6)
        self.assertEqual(RatingRule.objects.count(), 4)
        self.assertFalse(get_user_model().objects.exists())
        discipline = Discipline.objects.get(code='algorithm')
        discipline.name = 'Название федерации'
        discipline.save()
        call_command('prepare_project', stdout=output)
        self.assertEqual(Discipline.objects.get(pk=discipline.pk).name, 'Название федерации')
        self.assertEqual(Discipline.objects.count(), 6)

    def test_initial_account_has_hashed_password_consent_and_no_staff_bypass(self):
        output = StringIO()
        with patch('federation.management.commands.bootstrap_federation.getpass', return_value='StrongFspPass9!local'), patch('builtins.input', return_value='да'):
            call_command('bootstrap_federation', username='owner', email='owner@example.test', stdout=output)
        user = get_user_model().objects.get(username='owner')
        self.assertTrue(user.check_password('StrongFspPass9!local'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.profile.roles, ['federation'])
        self.assertTrue(Consent.objects.filter(user=user, kind='personal_data').exists())
        self.assertNotIn('StrongFspPass9!local', output.getvalue())

    def test_existing_federation_and_missing_consent_are_preserved(self):
        user = get_user_model().objects.create_user('existing')
        Profile.objects.create(user=user, roles=['federation'])
        with self.assertRaises(CommandError):
            call_command('bootstrap_federation', username='second', email='second@example.test')
        self.assertEqual(get_user_model().objects.count(), 1)
        user.profile.delete()
        with patch('federation.management.commands.bootstrap_federation.getpass', return_value='StrongFspPass9!local'), patch('builtins.input', return_value='нет'):
            with self.assertRaises(CommandError):
                call_command('bootstrap_federation', username='second', email='second@example.test')
        self.assertEqual(get_user_model().objects.count(), 1)
