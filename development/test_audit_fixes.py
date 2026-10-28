from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from federation.models import Application, Competition, Discipline, Organization, Profile, RankApplication, Result
from training.models import AssessmentNorm, AssessmentResult, OrganizationProgram, Stage, TrainingGroup
from training.services import athlete_training_history, enroll
from .models import RankCriterion
from .services import athlete_rank_path, preparation_organizations


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OptionalAuditFixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('prepare_project', stdout=StringIO())
        cls.today = timezone.localdate()
        cls.discipline = Discipline.objects.get(code='algorithm')
        cls.org = Organization.objects.create(name='Организация с подготовкой', municipality='Оренбург')
        cls.unrelated_org = Organization.objects.create(name='Только в реестре', municipality='Орск')
        cls.athlete = cls.person('path-athlete', ['athlete'])
        cls.other = cls.person('path-other', ['athlete'])
        cls.coach = cls.person('path-coach', ['coach'])
        cls.fed = cls.person('path-fed', ['federation'])
        comp = Competition.objects.create(title='Источник разряда', starts_on=cls.today, ends_on=cls.today,
            registration_opens=cls.today, registration_closes=cls.today, location='Тест',
            state='finished', protocol_published=True, plan_number='SOURCE-001')
        comp.disciplines.add(cls.discipline)
        application = Application.objects.create(competition=comp, athlete=cls.athlete, discipline=cls.discipline,
                                                 submitted_by=cls.athlete.user, status='approved')
        cls.result = Result.objects.create(application=application, place=1, score=120)
        cls.rank = RankApplication.objects.create(athlete=cls.athlete, submitted_by=cls.athlete.user, result=cls.result,
            requested_title='II разряд', status='approved', assigned_on=cls.today-timedelta(days=10),
            valid_until=cls.today+timedelta(days=300), order_number='RANK-SOURCE')
        for title in ('III разряд', 'II разряд', 'I разряд'):
            RankCriterion.objects.create(discipline=cls.discipline, rank_title=title, levels=['regional'],
                max_place=3, min_score=100, source_text='Проверочный критерий', is_demo=True)
        OrganizationProgram.objects.create(organization=cls.org, discipline=cls.discipline, stage=Stage.INITIAL)
        cls.group = TrainingGroup.objects.create(name='Учебная группа', organization=cls.org, discipline=cls.discipline,
                                                stage=Stage.INITIAL, coach=cls.coach)
        for person in (cls.athlete, cls.other):
            enroll(person, cls.group, cls.today-timedelta(days=20), 'Оренбург', 'female')
        cls.norm = AssessmentNorm.objects.create(discipline=cls.discipline, stage=Stage.INITIAL, name='Исходный норматив',
            unit='баллов', threshold=100, direction='gte', source='https://rules.example.test/version-1', demonstration=False)

    @classmethod
    def person(cls, name, roles):
        user = get_user_model().objects.create_user(name, email=name+'@example.test', first_name='Тестовый', last_name=name)
        profile = Profile.objects.create(user=user, roles=roles, birth_date=date(cls.today.year-20, 1, 1),
                                         organization=cls.org, municipality='Оренбург', start_year=cls.today.year-3)
        profile.disciplines.add(cls.discipline)
        return profile

    def test_second_rank_next_step_is_first_not_third(self):
        step = athlete_rank_path(self.athlete)[0]
        self.assertEqual(step['current'].requested_title, 'II разряд')
        self.assertEqual(step['target_title'], 'I разряд')
        self.assertEqual([item.rank_title for item in step['criteria']], ['I разряд'])
        self.client.force_login(self.athlete.user)
        response = self.client.get(reverse('development:pathway'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['rank_steps'][0]['target_title'], 'I разряд')

    def test_missing_next_criteria_does_not_fall_back_to_lower_rank(self):
        RankCriterion.objects.filter(rank_title='I разряд').delete()
        step = athlete_rank_path(self.athlete)[0]
        self.assertEqual(step['target_title'], 'I разряд')
        self.assertEqual(step['criteria'], [])
        self.assertEqual(step['status'], 'not_configured')

    def test_expired_rank_is_offered_for_confirmation_not_as_current(self):
        self.rank.valid_until = self.today-timedelta(days=1)
        self.rank.save()
        step = athlete_rank_path(self.athlete)[0]
        self.assertIsNone(step['current'])
        self.assertEqual(step['target_title'], 'II разряд')
        self.assertEqual(step['status'], 'confirmation')

    def test_preparation_indicator_does_not_count_unrelated_registry_organization(self):
        self.assertEqual(list(preparation_organizations()), [self.org])
        self.client.force_login(self.fed.user)
        response = self.client.get(reverse('development:index'))
        self.assertEqual(list(response.context['organizations']), [self.org])
        self.assertEqual(response.context['registry_organization_count'], 2)

    def test_profile_norm_history_keeps_original_name_source_and_threshold_and_scopes_owner(self):
        saved = AssessmentResult.objects.create(athlete=self.athlete, norm=self.norm, value=120,
                                                tested_on=self.today, recorded_by=self.coach.user)
        AssessmentResult.objects.create(athlete=self.other, norm=self.norm, value=777, tested_on=self.today, recorded_by=self.coach.user)
        self.norm.name = 'Новое название'
        self.norm.source = 'https://rules.example.test/version-2'
        self.norm.threshold = 200
        self.norm.save()
        history = athlete_training_history(self.athlete)
        self.assertEqual([item.pk for item in history['assessments']], [saved.pk])
        item = history['assessments'][0]
        self.assertEqual(item.history_norm['name'], 'Исходный норматив')
        self.assertEqual(item.history_norm['source'], 'https://rules.example.test/version-1')
        self.assertEqual(item.threshold_snapshot, 100)
        self.assertTrue(item.passed)
        self.client.force_login(self.athlete.user)
        for path in (reverse('profile'), reverse('athlete_detail', args=[self.athlete.pk])):
            response = self.client.get(path)
            self.assertContains(response, 'Исходный норматив')
            self.assertContains(response, 'https://rules.example.test/version-1')
            self.assertNotContains(response, 'version-2')
        self.assertEqual(self.client.get(reverse('athlete_detail', args=[self.other.pk])).status_code, 403)
