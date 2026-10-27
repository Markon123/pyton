from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from federation.models import CoachLink, Discipline, Organization, Profile
from .models import (AssessmentNorm, AssessmentResult, Attendance, Attestation, Enrollment,
                     OrganizationProgram, Session, Stage, TrainingGroup)
from .services import enroll, participation_rows, readiness, transfer_enrollment


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TrainingWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.discipline = Discipline.objects.create(name='Алгоритмы (тест)', code='training-test-algo')
        cls.other_discipline = Discipline.objects.create(name='Роботы (тест)', code='training-test-robot')
        cls.org = Organization.objects.create(name='Фиктивная организация', municipality='Оренбург')
        cls.other_org = Organization.objects.create(name='Другая фиктивная организация', municipality='Орск')
        cls.fed = cls.person('train-fed', ['federation'])
        cls.coach = cls.person('train-coach', ['coach'])
        cls.other_coach = cls.person('train-other-coach', ['coach'])
        cls.athlete = cls.person('train-athlete', ['athlete'])
        cls.other_athlete = cls.person('train-other-athlete', ['athlete'])
        cls.stranger = cls.person('train-stranger', ['athlete'])
        for org in (cls.org, cls.other_org):
            for stage in Stage.values:
                OrganizationProgram.objects.create(organization=org, discipline=cls.discipline, stage=stage)
        cls.group = TrainingGroup.objects.create(name='Начальная группа', organization=cls.org, discipline=cls.discipline,
                                                stage=Stage.INITIAL, coach=cls.coach)
        cls.next_group = TrainingGroup.objects.create(name='Следующий этап', organization=cls.other_org,
                                                     discipline=cls.discipline, stage=Stage.TRAINING, coach=cls.other_coach)
        cls.other_group = TrainingGroup.objects.create(name='Чужая группа', organization=cls.org,
                                                       discipline=cls.discipline, stage=Stage.INITIAL, coach=cls.other_coach)
        CoachLink.objects.create(coach=cls.coach, athlete=cls.athlete, discipline=cls.discipline, status='active')
        CoachLink.objects.create(coach=cls.other_coach, athlete=cls.other_athlete, discipline=cls.discipline, status='active')
        cls.enrollment = enroll(cls.athlete, cls.group, cls.today - timedelta(days=30), 'Оренбург', 'female')
        cls.norm = AssessmentNorm.objects.create(discipline=cls.discipline, stage=Stage.INITIAL, name='Печать', unit='знаков/мин',
                                                threshold=200, direction='gte', demonstration=True)
        cls.norm2 = AssessmentNorm.objects.create(discipline=cls.discipline, stage=Stage.INITIAL, name='Время задания', unit='сек',
                                                 threshold=90, direction='lte', demonstration=True)

    @classmethod
    def person(cls, username, roles):
        user = get_user_model().objects.create_user(username=username, password='TrainingTest2026!', first_name=username,
                                                     email=f'{username}@example.test')
        profile = Profile.objects.create(user=user, roles=roles, organization=cls.org, birth_date=date(2005, 1, 1),
                                         municipality='Оренбург', start_year=2020)
        profile.disciplines.set([cls.discipline, cls.other_discipline])
        return profile

    def use(self, person, role=None):
        self.client.force_login(person.user)
        session = self.client.session
        session['active_role'] = role or person.roles[0]
        session.save()

    def add_result(self, norm=None, value=240, when=None):
        result = AssessmentResult(athlete=self.athlete, norm=norm or self.norm, value=value,
                                  tested_on=when or self.today - timedelta(days=1), recorded_by=self.coach.user)
        result.full_clean()
        result.save()
        return result

    def pass_all(self):
        self.add_result(self.norm, 240)
        self.add_result(self.norm2, 75)

    def test_federation_group_enrollment_session_attendance_workflow(self):
        self.use(self.fed)
        response = self.client.post(reverse('training:enroll', args=[self.group.pk]), {
            'athlete': self.stranger.pk, 'start_date': self.today, 'municipality': 'Орск', 'sex': 'male'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Enrollment.objects.filter(group=self.group, athlete=self.stranger).exists())
        response = self.client.post(reverse('training:session_create', args=[self.group.pk]), {
            'date': self.today, 'topic': 'Разбор задач', 'format': 'online', 'confirmation_url': 'https://example.test/log/lesson'})
        self.assertEqual(response.status_code, 302)
        lesson = Session.objects.get(topic='Разбор задач')
        response = self.client.post(reverse('training:session_detail', args=[lesson.pk]), {'present': [self.athlete.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Attendance.objects.get(session=lesson, athlete=self.athlete).present)
        self.assertFalse(Attendance.objects.get(session=lesson, athlete=self.stranger).present)
        self.assertEqual(self.client.post(reverse('training:session_detail', args=[lesson.pk]), {}).status_code, 302)
        self.assertFalse(Attendance.objects.get(session=lesson, athlete=self.athlete).present)

    def test_coach_cannot_enroll_unlinked_athlete_or_manage_other_group(self):
        self.use(self.coach)
        response = self.client.post(reverse('training:enroll', args=[self.group.pk]), {
            'athlete': self.stranger.pk, 'start_date': self.today, 'municipality': 'Оренбург', 'sex': 'female'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Enrollment.objects.filter(athlete=self.stranger).exists())
        self.assertEqual(self.client.get(reverse('training:group_detail', args=[self.other_group.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('training:session_create', args=[self.other_group.pk]), {
            'date': self.today, 'topic': 'Подмена', 'format': 'offline'}).status_code, 403)

    def test_attendance_cannot_include_unenrolled_athlete(self):
        self.use(self.fed)
        lesson = Session.objects.create(group=self.group, date=self.today, topic='Тренировка', created_by=self.fed.user)
        response = self.client.post(reverse('training:session_detail', args=[lesson.pk]), {'present': [self.stranger.pk]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Attendance.objects.count(), 0)
        with self.assertRaises(ValidationError):
            Attendance(session=lesson, athlete=self.stranger, present=True).full_clean()

    def test_athlete_sees_only_own_history_and_cannot_write(self):
        enroll(self.other_athlete, self.group, self.today - timedelta(days=10), 'Оренбург')
        self.use(self.athlete)
        response = self.client.get(reverse('training:group_detail', args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.athlete.display_name)
        self.assertNotContains(response, self.other_athlete.display_name)
        self.assertEqual(self.client.post(reverse('training:enroll', args=[self.group.pk]), {}).status_code, 403)
        self.use(self.stranger)
        self.assertEqual(self.client.get(reverse('training:group_detail', args=[self.group.pk])).status_code, 403)

    def test_both_threshold_directions_and_missing_requirement(self):
        self.assertFalse(readiness(self.enrollment)['ready'])
        first = self.add_result(self.norm, 200)
        self.assertTrue(first.passed)
        self.assertFalse(readiness(self.enrollment)['ready'])
        failed = self.add_result(self.norm2, 91)
        self.assertFalse(failed.passed)
        self.assertFalse(readiness(self.enrollment)['ready'])
        self.add_result(self.norm2, 90)
        self.assertTrue(readiness(self.enrollment)['ready'])

    def test_old_results_do_not_pass_and_zero_required_is_not_ready(self):
        self.enrollment.start_date = self.today - timedelta(days=500)
        # Adjust fixture directly: runtime history cannot be rewritten through forms.
        Enrollment.objects.filter(pk=self.enrollment.pk).update(start_date=self.enrollment.start_date)
        self.add_result(self.norm, 240, self.today - timedelta(days=400))
        self.add_result(self.norm2, 75, self.today - timedelta(days=400))
        self.assertFalse(readiness(self.enrollment)['ready'])
        AssessmentNorm.objects.update(required=False)
        state = readiness(self.enrollment)
        self.assertFalse(state['ready'])
        self.assertTrue(state['missing_norms'])

    def test_history_snapshot_preserved_when_norm_changes(self):
        result = self.add_result(self.norm, 210)
        self.assertTrue(result.passed)
        self.norm.threshold = 250
        self.norm.save()
        result.refresh_from_db()
        self.assertEqual(result.threshold_snapshot, Decimal('200'))
        self.assertTrue(result.passed)
        self.assertFalse(readiness(self.enrollment)['ready'])

    def test_full_attestation_transfer_preserves_nonoverlapping_history(self):
        self.pass_all()
        self.use(self.coach)
        response = self.client.post(reverse('training:transfer', args=[self.enrollment.pk]), {
            'target_group': self.next_group.pk, 'date': self.today})
        self.assertEqual(response.status_code, 302)
        record = Attestation.objects.get(enrollment=self.enrollment)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.end_date, self.today - timedelta(days=1))
        self.assertEqual(record.new_enrollment.start_date, self.today)
        self.assertEqual(record.new_enrollment.group, self.next_group)
        self.assertEqual(record.new_enrollment.municipality, 'Оренбург')
        self.assertEqual(len(record.evidence), 2)
        self.assertEqual(Enrollment.objects.filter(athlete=self.athlete).count(), 2)
        with self.assertRaises(ValidationError):
            transfer_enrollment(self.enrollment, self.next_group, self.today, self.coach.user)

    def test_attestation_cannot_be_forced_without_norms_or_on_first_day(self):
        self.use(self.fed)
        response = self.client.post(reverse('training:transfer', args=[self.enrollment.pk]), {
            'target_group': self.next_group.pk, 'date': self.today})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Attestation.objects.count(), 0)
        self.enrollment.refresh_from_db()
        self.assertIsNone(self.enrollment.end_date)
        self.pass_all()
        with self.assertRaises(ValidationError):
            transfer_enrollment(self.enrollment, self.next_group, self.enrollment.start_date, self.fed.user)

    def test_overlapping_enrollment_rejected_but_distinct_disciplines_allowed(self):
        with self.assertRaises(ValidationError):
            enroll(self.athlete, self.other_group, self.today, 'Орск')
        OrganizationProgram.objects.create(organization=self.org, discipline=self.other_discipline, stage=Stage.INITIAL)
        robots = TrainingGroup.objects.create(name='Роботы', organization=self.org, discipline=self.other_discipline, stage=Stage.INITIAL, coach=self.coach)
        record = enroll(self.athlete, robots, self.today, 'Оренбург')
        self.assertEqual(record.group.discipline, self.other_discipline)

    def test_census_reports_historical_organization_and_municipality(self):
        Enrollment.objects.filter(pk=self.enrollment.pk).update(start_date=date(self.today.year - 1, 1, 1), end_date=date(self.today.year - 1, 12, 31))
        enroll(self.athlete, self.next_group, date(self.today.year, 1, 1), 'Орск', 'female')
        rows = participation_rows(self.today.year - 1, self.today.year)
        self.assertEqual({(r['year'], r['organization_id']) for r in rows}, {
            (self.today.year - 1, self.org.pk), (self.today.year, self.other_org.pk)})
        self.assertEqual(len(participation_rows(self.today.year - 1, self.today.year, organization=self.org.pk)), 1)
        self.assertEqual(len(participation_rows(self.today.year - 1, self.today.year, municipality='Орск')), 1)
        self.assertEqual(participation_rows(self.today.year - 1, self.today.year, age_max=5), [])

    def test_demonstration_norm_and_online_confirmation_validation(self):
        self.use(self.fed)
        response = self.client.post(reverse('training:norm_create'), {'name': 'Непроверенный', 'discipline': self.discipline.pk,
            'stage': 'initial', 'sex': 'all', 'unit': 'сек', 'threshold': 1, 'direction': 'lte', 'required': 'on', 'active': 'on'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AssessmentNorm.objects.filter(name='Непроверенный').exists())
        response = self.client.post(reverse('training:session_create', args=[self.group.pk]), {
            'date': self.today, 'topic': 'Онлайн без подтверждения', 'format': 'online'})
        self.assertEqual(response.status_code, 400)

    def test_assessment_form_blocks_wrong_athlete_and_stage(self):
        self.use(self.coach)
        response = self.client.post(reverse('training:assessment_create', args=[self.group.pk]), {
            'athlete': self.stranger.pk, 'norm': self.norm.pk, 'value': 250, 'tested_on': self.today})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AssessmentResult.objects.count(), 0)
        response = self.client.post(reverse('training:assessment_create', args=[self.group.pk]), {
            'athlete': self.athlete.pk, 'norm': self.norm.pk, 'value': 250, 'tested_on': self.today})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssessmentResult.objects.get().passed)

    def test_group_metadata_with_history_cannot_move_organization(self):
        self.group.organization = self.other_org
        with self.assertRaises(ValidationError):
            self.group.full_clean()

    def test_unspecified_sex_cannot_bypass_sex_specific_norms(self):
        self.enrollment.sex = 'unspecified'
        self.enrollment.save(update_fields=['sex'])
        self.pass_all()
        AssessmentNorm.objects.create(discipline=self.discipline, stage=Stage.INITIAL, name='Порог по полу',
                                      unit='сек', threshold=10, direction='lte', sex='female', demonstration=True)
        state = readiness(self.enrollment)
        self.assertTrue(state['missing_sex'])
        self.assertFalse(state['ready'])
        self.use(self.fed)
        response = self.client.post(reverse('training:enrollment_info', args=[self.enrollment.pk]),
                                    {'municipality': 'Оренбург', 'sex': 'female'})
        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.sex, 'female')
        self.assertFalse(readiness(self.enrollment)['ready'])  # New mandatory norm is still missing.

    def test_session_edit_cannot_move_recorded_attendance_outside_enrollment(self):
        lesson = Session.objects.create(group=self.group, date=self.today, topic='Старая тема', created_by=self.fed.user)
        Attendance.objects.create(session=lesson, athlete=self.athlete, present=True)
        self.use(self.fed)
        response = self.client.post(reverse('training:session_edit', args=[lesson.pk]), {
            'date': self.enrollment.start_date - timedelta(days=1), 'topic': 'Новая тема', 'format': 'offline'})
        self.assertEqual(response.status_code, 400)
        lesson.refresh_from_db()
        self.assertEqual(lesson.topic, 'Старая тема')
        response = self.client.post(reverse('training:session_edit', args=[lesson.pk]), {
            'date': self.today, 'topic': 'Исправленная тема', 'format': 'offline'})
        self.assertEqual(response.status_code, 302)
        lesson.refresh_from_db()
        self.assertEqual(lesson.topic, 'Исправленная тема')

    def test_exam_on_certification_date_retains_valid_provenance(self):
        first = self.add_result(self.norm, 240, self.today)
        self.add_result(self.norm2, 75, self.today)
        transfer_enrollment(self.enrollment, self.next_group, self.today, self.fed.user)
        first.refresh_from_db()
        first.full_clean()  # The source attestation explicitly preserves exam-day provenance.
