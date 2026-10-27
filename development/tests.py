from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from federation.models import Application, CoachLink, Competition, DictionaryEntry, Discipline, JudgeAssignment, Organization, Profile, Result
from .forms import CriterionForm
from .models import Evidence, ParticipationCertificate, RankCriterion
from .services import certificate_url, rank_candidates


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DevelopmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name='Демоорганизация', municipality='Оренбург')
        cls.discipline = Discipline.objects.create(name='Алгоритмы', code='algorithms')
        cls.other_disc = Discipline.objects.create(name='Роботы', code='robotics')
        cls.fed = cls.person('fed_dev', ['federation'])
        cls.athlete = cls.person('sport_dev', ['athlete'])
        cls.other = cls.person('other_dev', ['athlete'])
        cls.coach = cls.person('coach_dev', ['coach'])
        cls.judge = cls.person('judge_dev', ['judge'])
        cls.ministry = cls.person('ministry_dev', ['ministry'])
        cls.competition = Competition.objects.create(title='Тестовый чемпионат развития', starts_on=cls.today,
            ends_on=cls.today, registration_opens=cls.today - timedelta(days=14), registration_closes=cls.today,
            location='Демостенд', state='finished', protocol_published=True, plan_number='DEMO-5')
        cls.competition.disciplines.add(cls.discipline)
        cls.application = Application.objects.create(competition=cls.competition, athlete=cls.athlete,
            discipline=cls.discipline, status='approved', submitted_by=cls.athlete.user)
        cls.result = Result.objects.create(application=cls.application, place=1, score=100)
        cls.rule = RankCriterion.objects.create(discipline=cls.discipline, rank_title='III разряд', levels=['regional'],
            max_place=3, min_score=90, age_min=12, age_max=30, source_text='Тестовый демонстрационный порог', is_demo=True)
        cls.assignment = JudgeAssignment.objects.create(competition=cls.competition, judge=cls.judge, position='Главный судья')

    @classmethod
    def person(cls, name, roles):
        user = get_user_model().objects.create_user(name, f'{name}@example.test', 'DemoStrongTest2026!')
        user.first_name = 'Тестимя'
        user.last_name = 'Закрытаяфамилия' + name
        user.save()
        profile = Profile.objects.create(user=user, roles=roles, birth_date=date(cls.today.year - 20, 1, 15),
            organization=cls.org, municipality='Оренбург', start_year=cls.today.year - 4, guardian_name='ЗакрытыйПредставитель')
        profile.disciplines.add(cls.discipline)
        return profile

    def setUp(self):
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        settings = override_settings(MEDIA_ROOT=self.files.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def login_as(self, profile, role=None):
        self.client.force_login(profile.user)
        session = self.client.session
        session['active_role'] = role or profile.roles[0]
        session.save()

    def issue(self, profile=None):
        self.login_as(profile or self.athlete)
        response = self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'})
        self.assertEqual(response.status_code, 302)
        return ParticipationCertificate.objects.latest('pk')

    def test_issue_download_qr_public_minimal_data(self):
        record = self.issue()
        self.assertTrue(record.valid)
        with override_settings(PUBLIC_BASE_URL='https://demo.example.test'):
            self.assertEqual(certificate_url(record), f'https://demo.example.test/development/verify/{record.number}/')
            import qrcode
            with patch('qrcode.make', wraps=qrcode.make) as maker:
                response = self.client.get(reverse('development:certificate_download', args=[record.number]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content.startswith(b'%PDF'))
                self.assertIn(b'/Subtype /Image', response.content)
                maker.assert_called_once_with(certificate_url(record))
        public = Client().get(reverse('development:verify', args=[record.number]))
        self.assertContains(public, 'Действительна')
        self.assertContains(public, 'Тестовый чемпионат развития')
        for private in [self.athlete.display_name, self.athlete.user.email, self.athlete.guardian_name,
                        self.athlete.birth_date.strftime('%d.%m.%Y')]:
            self.assertNotContains(public, private)

    def test_certificate_requires_published_result_and_permission(self):
        self.login_as(self.other)
        response = self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ParticipationCertificate.objects.exists())
        self.login_as(self.athlete)
        self.competition.protocol_published = False
        self.competition.save()
        self.assertEqual(self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'}).status_code, 400)

    def test_public_consent_is_required(self):
        self.login_as(self.athlete)
        self.assertEqual(self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk}).status_code, 400)
        self.assertFalse(ParticipationCertificate.objects.exists())

    def test_coach_access_requires_matching_confirmed_discipline(self):
        self.login_as(self.coach)
        link = CoachLink.objects.create(coach=self.coach, athlete=self.athlete, discipline=self.other_disc, status='active')
        self.assertEqual(self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'}).status_code, 400)
        link.discipline = self.discipline
        link.save()
        self.assertEqual(self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'}).status_code, 302)

    def test_reissue_revocation_and_changed_result_invalidate_number(self):
        old = self.issue()
        new = self.issue()
        old.refresh_from_db()
        self.assertFalse(old.valid)
        self.assertTrue(new.valid)
        self.result.score = Decimal('99')
        self.result.save()
        new.refresh_from_db()
        self.assertFalse(new.valid)
        response = Client().get(reverse('development:verify', args=[new.number]))
        self.assertContains(response, 'НЕДЕЙСТВИТЕЛЬНА')

    def test_private_download_and_revoke_are_owner_scoped(self):
        record = self.issue()
        self.login_as(self.other)
        self.assertEqual(self.client.get(reverse('development:certificate_download', args=[record.number])).status_code, 403)
        self.assertEqual(self.client.post(reverse('development:certificate_revoke', args=[record.number])).status_code, 403)
        self.login_as(self.athlete)
        self.assertEqual(self.client.post(reverse('development:certificate_revoke', args=[record.number])).status_code, 302)
        record.refresh_from_db()
        self.assertFalse(record.valid)

    def test_candidates_match_exact_published_criterion(self):
        self.assertEqual([(r['athlete'].pk, r['criterion'].pk) for r in rank_candidates()], [(self.athlete.pk, self.rule.pk)])
        self.assertEqual(rank_candidates([self.other.pk]), [])
        self.rule.min_score = 101
        self.rule.save()
        self.assertEqual(rank_candidates(), [])
        self.rule.min_score, self.rule.age_min, self.rule.age_max = 0, 25, 30
        self.rule.save()
        self.assertEqual(rank_candidates(), [])
        self.rule.age_min, self.rule.discipline = 12, self.other_disc
        self.rule.save()
        self.assertEqual(rank_candidates(), [])
        self.rule.discipline, self.rule.levels = self.discipline, ['interregional']
        self.rule.save()
        self.assertEqual(rank_candidates(), [])

    def test_criterion_configuration_federation_only(self):
        payload = dict(discipline=self.discipline.pk, rank_title='II разряд', levels=['regional'], max_place=2,
                       min_score='80', age_min=12, age_max=30, source_text='Новый учебный пример', is_demo='on', active='on')
        self.login_as(self.fed)
        self.assertEqual(self.client.post(reverse('development:criteria'), payload).status_code, 302)
        self.assertTrue(RankCriterion.objects.filter(rank_title='II разряд').exists())
        self.login_as(self.athlete)
        self.assertEqual(self.client.post(reverse('development:criteria'), payload).status_code, 403)
        payload.pop('is_demo')
        form = CriterionForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn('source_url', form.errors)

    def test_criteria_accept_configured_rank_dictionary(self):
        DictionaryEntry.objects.create(kind='rank', code='custom-rank', label='Учебный дополнительный разряд')
        form = CriterionForm(dict(discipline=self.discipline.pk, rank_title='Учебный дополнительный разряд', levels=['regional'],
             max_place=2, min_score=0, age_min=12, age_max=30, source_text='Тестовый критерий', is_demo='on', active='on'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().rank_title, 'Учебный дополнительный разряд')

    def test_pathway_uses_age_discipline_and_private_scope(self):
        future = Competition.objects.create(title='Подходящий будущий старт', starts_on=self.today + timedelta(days=10),
            ends_on=self.today + timedelta(days=10), registration_opens=self.today, registration_closes=self.today + timedelta(days=5),
            location='Тест', state='open', age_min=12, age_max=25)
        future.disciplines.add(self.discipline)
        self.login_as(self.athlete)
        response = self.client.get(reverse('development:pathway'))
        self.assertContains(response, 'Подходящий будущий старт')
        self.assertContains(response, 'РУСАДА')
        self.assertEqual(self.client.get(reverse('development:pathway'), {'athlete': self.other.pk}).status_code, 404)
        self.login_as(self.ministry)
        self.assertEqual(self.client.get(reverse('development:pathway')).status_code, 403)

    def test_evidence_scoped_and_invalid_upload_rejected(self):
        self.login_as(self.fed)
        payload = {'year': self.today.year, 'indicator': 'competitions', 'citation': 'Тестовое основание',
                   'document': SimpleUploadedFile('demo.pdf', b'%PDF-1.4\nfictitious', content_type='application/pdf')}
        self.assertEqual(self.client.post(reverse('development:evidence_create'), payload).status_code, 302)
        record = Evidence.objects.get()
        self.login_as(self.ministry)
        self.assertEqual(self.client.get(reverse('development:evidence_download', args=[record.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('development:evidence_create'), {'year': self.today.year}).status_code, 403)
        self.login_as(self.athlete)
        self.assertEqual(self.client.get(reverse('development:evidence_download', args=[record.pk])).status_code, 403)
        self.login_as(self.fed)
        payload['document'] = SimpleUploadedFile('fake.pdf', b'HTML fake PDF')
        self.assertEqual(self.client.post(reverse('development:evidence_create'), payload).status_code, 400)

    def test_judge_documents_real_xlsx_and_private_history(self):
        self.login_as(self.judge)
        response = self.client.get(reverse('development:judging_document', args=['panel', self.competition.pk, 'xlsx']))
        self.assertEqual(response.status_code, 200)
        values = list(load_workbook(BytesIO(response.content)).active.values)
        self.assertIn(self.judge.display_name, [str(c) for row in values for c in row])
        self.assertEqual(self.client.get(reverse('development:judging_document', args=['history', self.judge.pk, 'pdf'])).status_code, 200)
        self.login_as(self.ministry)
        self.assertEqual(self.client.get(reverse('development:judging_document', args=['panel', self.competition.pk, 'docx'])).status_code, 200)
        self.assertEqual(self.client.get(reverse('development:judging_document', args=['history', self.judge.pk, 'xlsx'])).status_code, 403)

    def test_region_indicators_render_and_ministry_read_only(self):
        self.login_as(self.ministry)
        self.assertContains(self.client.get(reverse('development:index')), 'Тестовый чемпионат развития')
        self.assertEqual(self.client.post(reverse('development:criteria'), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('development:certificate_issue'), {}).status_code, 403)

    def test_public_verification_disallows_post(self):
        record = self.issue()
        self.assertEqual(Client().post(reverse('development:verify', args=[record.number]), {}).status_code, 400)
