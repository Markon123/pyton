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
    def test_local_config_has_matching_http_origins_unique_secrets_and_no_shared_password(self):
        with TemporaryDirectory() as folder:
            first = create_config(None, Path(folder) / '.env.local', local=True, port=8016)
            second = create_config(None, Path(folder) / '.env.other', local=True, port=8000)
            def values(path):
                return dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#'))
            a, b = values(first), values(second)
            self.assertEqual(a['PUBLIC_BASE_URL'], 'http://localhost:8016')
            self.assertEqual(a['FSP_PORT'], '8016')
            self.assertEqual(a['DJANGO_CSRF_TRUSTED_ORIGINS'], 'http://localhost:8016,http://127.0.0.1:8016')
            self.assertEqual(a['DJANGO_DEBUG'], '1')
            self.assertEqual(a['TRUST_PROXY_HEADERS'], '0')
            self.assertEqual(a['FSP_MINISTRY_PASSWORD'], '')
            self.assertNotEqual(a['DJANGO_SECRET_KEY'], b['DJANGO_SECRET_KEY'])
            self.assertNotEqual(a['POSTGRES_PASSWORD'], b['POSTGRES_PASSWORD'])
            with self.assertRaises(FileExistsError):
                create_config(None, first, local=True)

    def test_invalid_local_ports_and_mixed_mode_do_not_create_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '.env.local'
            for port in (0, 1023, 65536, True, '8000'):
                with self.assertRaises(ValueError):
                    create_config(None, path, local=True, port=port)
            with self.assertRaises(ValueError):
                create_config('example.org', path, local=True)
            self.assertFalse(path.exists())

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

    def test_direct_federation_bootstrap_cannot_bypass_ministry(self):
        with self.assertRaises(CommandError):
            call_command('bootstrap_federation', username='owner', email='owner@example.test')
        self.assertFalse(get_user_model().objects.exists())
