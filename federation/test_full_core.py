from datetime import date
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from federation.models import Profile, Organization, Discipline, Competition, DocumentLayout, DictionaryEntry
from federation.services import census, census_breakdown
from training.models import TrainingGroup, Enrollment


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CompleteCoreTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('fulltest', password='Testpass2026!', first_name='Тест')
        self.discipline = Discipline.objects.create(name='Алгоритмы', code='alg')
        self.old_org = Organization.objects.create(name='Прошлая организация', municipality='Орск')
        self.new_org = Organization.objects.create(name='Новая организация', municipality='Оренбург')
        self.person = Profile.objects.create(user=self.user, roles=['federation', 'athlete', 'coach'], birth_date=date(2000, 1, 1),
                                            organization=self.new_org, start_year=2020, municipality='Оренбург')
        self.person.disciplines.add(self.discipline)

    def test_public_guide_contains_real_organizations_without_login(self):
        response = self.client.get(reverse('guide'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новая организация')
        self.assertContains(response, reverse('register'))

    def test_system_checks_work_before_first_database_migration(self):
        import os, subprocess, sys
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from django.conf import settings
        with TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, 'manage.py', 'check'], cwd=settings.BASE_DIR,
                env={**os.environ, 'DATABASE_URL': '', 'DATABASE_PATH': str(Path(folder) / 'new.sqlite3')},
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_history_keeps_old_organization_and_municipality(self):
        old_group = TrainingGroup.objects.create(name='Прошлая', organization=self.old_org, discipline=self.discipline, coach=self.person, stage='initial')
        new_group = TrainingGroup.objects.create(name='Новая', organization=self.new_org, discipline=self.discipline, coach=self.person, stage='training')
        Enrollment.objects.create(athlete=self.person, group=old_group, start_date=date(2023, 1, 1), end_date=date(2024, 12, 31), municipality='Орск')
        Enrollment.objects.create(athlete=self.person, group=new_group, start_date=date(2025, 1, 1), municipality='Оренбург')
        self.assertEqual(census(2024, 2025), [{'year': 2024, 'total': 1}, {'year': 2025, 'total': 1}])
        self.assertEqual(census(2024, 2025, organization=self.old_org.pk), [{'year': 2024, 'total': 1}, {'year': 2025, 'total': 0}])
        self.assertEqual(census(2024, 2025, municipality='Орск'), [{'year': 2024, 'total': 1}, {'year': 2025, 'total': 0}])
        labels = census_breakdown(2024, 2025)
        self.assertTrue(any(x['label'] == '18–25 лет' for x in labels))

    def test_ministry_cannot_modify_own_profile_or_configuration(self):
        self.person.roles = ['ministry']
        self.person.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('profile'), {'first_name': 'Изменено'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('configuration'), {}).status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Тест')

    def test_ministry_can_export_aggregate_census(self):
        self.person.roles = ['ministry']
        self.person.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse('export_document', args=['census', 0, 'xlsx']))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'PK'))

    def test_dictionary_edit_requires_federation_active_role(self):
        self.client.force_login(self.user)
        session = self.client.session; session['active_role'] = 'athlete'; session.save()
        self.assertEqual(self.client.post(reverse('configuration'), {'kind': 'municipality', 'code': 'x', 'label': 'X', 'metadata': '{}', 'active': 'on'}).status_code, 403)
        self.assertEqual(DictionaryEntry.objects.count(), 0)

    def test_document_layout_applies_to_actual_export(self):
        self.client.force_login(self.user)
        DocumentLayout.objects.create(kind='census', heading='Мой проверенный заголовок', footer='Подписант тестовый')
        response = self.client.get(reverse('export_document', args=['census', 0, 'docx']))
        from io import BytesIO
        from docx import Document
        document = Document(BytesIO(response.content))
        content = '\n'.join(p.text for p in document.paragraphs)
        self.assertIn('Мой проверенный заголовок', content)
        self.assertIn('Подписант тестовый', content)

    def test_first_enrollment_preserves_known_legacy_years(self):
        before = census(2024, 2025)
        group = TrainingGroup.objects.create(name='Первая группа', organization=self.new_org, discipline=self.discipline, coach=self.person, stage='initial')
        Enrollment.objects.create(athlete=self.person, group=group, start_date=date(2026, 1, 1), municipality='Оренбург')
        self.assertEqual(census(2024, 2025), before)
        self.assertEqual([r['total'] for r in before], [1, 1])

    def test_history_in_one_discipline_keeps_other_legacy_discipline(self):
        other = Discipline.objects.create(name='Продуктовое', code='product')
        self.person.disciplines.add(other)
        group = TrainingGroup.objects.create(name='Алгоритмы 2023', organization=self.new_org, discipline=self.discipline, coach=self.person, stage='initial')
        Enrollment.objects.create(athlete=self.person, group=group, start_date=date(2023, 1, 1), end_date=date(2024, 12, 31), municipality='Оренбург')
        self.assertEqual(census(2025, 2025, discipline=other.pk)[0]['total'], 1)
        self.assertEqual(census(2025, 2025, discipline=self.discipline.pk)[0]['total'], 0)

    def test_invalid_legacy_metadata_does_not_break_rank_choices(self):
        from federation.forms import configured_titles
        DictionaryEntry.objects.create(kind='rank', code='old-invalid', label='Тестовый разряд', metadata=['legacy invalid'])
        self.assertIn('Тестовый разряд', configured_titles('rank', []))
