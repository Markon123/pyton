"""Integration checks for permissions, provenance and the working MVP workflows."""
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import (
    Application, AuditLog, CoachLink, Competition, Consent, Discipline,
    Notification, Organization, Profile, RankApplication, RatingRule, Result, Team,
)


User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PlatformTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        override = override_settings(MEDIA_ROOT=temporary.name)
        override.enable()
        self.addCleanup(override.disable)

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.discipline = Discipline.objects.create(name='Программирование алгоритмическое', code='algorithms')
        cls.other_discipline = Discipline.objects.create(name='Робототехника', code='robotics')
        cls.organization = Organization.objects.create(name='Вымышленный клуб «Тест»', municipality='Оренбург')
        cls.federation = cls.make_profile('fed', ['federation', 'athlete'])
        cls.athlete = cls.make_profile('athlete', ['athlete'])
        cls.other = cls.make_profile('other', ['athlete'])
        cls.coach = cls.make_profile('coach', ['coach'])
        cls.judge = cls.make_profile('judge', ['judge'])
        for level, first, second, third in [
            ('regional', 40, 30, 20), ('interregional', 60, 45, 30),
            ('russian_championship', 100, 80, 60), ('russian_junior', 80, 60, 40),
        ]:
            RatingRule.objects.create(level=level, first=first, second=second, third=third)

    @classmethod
    def make_profile(cls, username, roles, birth_date=None):
        user = User.objects.create_user(username, f'{username}@example.test', 'TestStrongPassword2026!')
        user.first_name = 'Вымышленный'
        user.last_name = username
        user.save()
        profile = Profile.objects.create(
            user=user, roles=roles, birth_date=birth_date or date(cls.today.year - 20, 1, 1),
            municipality='Оренбург', organization=cls.organization, start_year=cls.today.year - 3,
        )
        profile.disciplines.add(cls.discipline)
        Consent.objects.create(user=user, kind='personal_data')
        return profile

    def login_as(self, person, role=None):
        self.client.force_login(person.user)
        session = self.client.session
        session['active_role'] = role or person.roles[0]
        session.save()

    def competition(self, **overrides):
        fields = dict(
            title='Вымышленный чемпионат', starts_on=self.today, ends_on=self.today,
            registration_opens=self.today - timedelta(days=14), registration_closes=self.today,
            location='Учебный стенд', state='open', age_min=12, age_max=99,
            plan_number='TEST-PLAN-001',
        )
        fields.update(overrides)
        if 'calendar_plan' not in overrides:
            fields['calendar_plan'] = 'regional' if fields.get('level', 'regional') == 'regional' else 'ekp'
        competition = Competition.objects.create(**fields)
        competition.disciplines.add(self.discipline)
        return competition

    def application(self, competition, person=None, status='approved', discipline=None):
        person = person or self.athlete
        return Application.objects.create(
            competition=competition, athlete=person, discipline=discipline or self.discipline,
            submitted_by=person.user, status=status,
        )

    def result(self, person=None, place=1, level='regional', when=None):
        competition = self.competition(
            title=f'Соревнование {Competition.objects.count() + 1}',
            starts_on=when or self.today, ends_on=when or self.today,
            registration_opens=(when or self.today) - timedelta(days=14),
            registration_closes=when or self.today, level=level, state='finished', protocol_published=True,
        )
        return Result.objects.create(application=self.application(competition, person), place=place, score=100)

    def test_full_competition_rank_and_export_workflow(self):
        """Federation -> athlete -> moderation -> complete protocol -> rank."""
        self.login_as(self.federation)
        payload = {
            'title': 'Интеграционный чемпионат', 'description': 'Вымышленный турнир',
            'disciplines': [self.discipline.pk], 'starts_on': self.today.isoformat(),
            'ends_on': self.today.isoformat(),
            'registration_opens': (self.today - timedelta(days=7)).isoformat(),
            'registration_closes': self.today.isoformat(), 'location': 'Тестовая площадка',
            'format': 'offline', 'level': 'regional', 'kind': 'Чемпионат',
            'age_min': 12, 'age_max': 99, 'calendar_plan': 'regional',
            'plan_number': 'TEST-1', 'state': 'draft',
            'regulation': SimpleUploadedFile('demo.pdf', b'%PDF-1.4\n% fictitious regulation\n', content_type='application/pdf'),
        }
        self.client.post(reverse('competition_create'), payload)
        competition = Competition.objects.get(title='Интеграционный чемпионат')
        self.client.post(reverse('competition_action', args=[competition.pk]), {'action': 'open'})
        competition.refresh_from_db()
        self.assertEqual(competition.state, 'open')

        self.login_as(self.athlete)
        self.client.post(reverse('application_create', args=[competition.pk]), {
            'athlete': self.athlete.pk, 'discipline': self.discipline.pk,
        })
        application = Application.objects.get(competition=competition, athlete=self.athlete)
        self.assertEqual(application.status, 'pending')

        self.login_as(self.federation)
        self.client.post(reverse('application_moderate', args=[application.pk]), {'status': 'approved'})
        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')
        self.client.post(reverse('competition_action', args=[competition.pk]), {'action': 'close'})
        self.client.post(reverse('result_save', args=[competition.pk]), {
            'application': application.pk, 'place': 1, 'score': '142.50',
        })
        result = Result.objects.get(application=application)
        self.assertEqual(result.score, Decimal('142.50'))
        self.client.post(reverse('competition_action', args=[competition.pk]), {'action': 'publish'})
        competition.refresh_from_db()
        self.assertTrue(competition.protocol_published)

        self.login_as(self.athlete)
        response = self.client.get(reverse('athlete_detail', args=[self.athlete.pk]))
        self.assertContains(response, 'Интеграционный чемпионат')
        self.client.post(reverse('qualifications'), {
            'kind': 'rank', 'athlete': self.athlete.pk, 'result': result.pk,
            'requested_title': 'III разряд',
        })
        qualification = RankApplication.objects.get(athlete=self.athlete, result=result)

        self.login_as(self.federation)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('qualification_review', args=[qualification.pk]), {
                'status': 'approved', 'assigned_on': self.today.isoformat(),
                'valid_until': (self.today + timedelta(days=365)).isoformat(),
                'order_number': 'ДЕМО-1', 'review_comment': 'Тестовое присвоение',
            })
        qualification.refresh_from_db()
        self.assertEqual(qualification.status, 'approved')
        self.assertEqual(qualification.order_number, 'ДЕМО-1')
        self.assertEqual(qualification.athlete.current_rank, qualification)
        document = self.client.get(reverse('export_document', args=['protocol', competition.pk, 'xlsx']))
        self.assertEqual(document.status_code, 200)
        workbook = load_workbook(BytesIO(document.content))
        data = '\n'.join(str(cell.value) for row in workbook.active for cell in row)
        self.assertIn(self.athlete.display_name, data)
        self.assertTrue(AuditLog.objects.filter(user=self.federation.user, role='federation').exists())
        self.assertTrue(AuditLog.objects.filter(user=self.athlete.user, role='athlete').exists())
        self.assertTrue(Notification.objects.filter(user=self.athlete.user).exists())
        self.assertTrue(mail.outbox)

    def test_active_role_restricts_privileged_account(self):
        self.login_as(self.federation, 'athlete')
        self.assertEqual(self.client.get(reverse('competition_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('audit')).status_code, 403)
        self.client.post(reverse('switch_role'), {'role': 'federation'})
        self.assertEqual(self.client.get(reverse('competition_create')).status_code, 200)

    def test_role_switch_cannot_grant_unowned_role_or_redirect_offsite(self):
        self.login_as(self.athlete)
        response = self.client.post(reverse('switch_role'), {'role': 'federation', 'next': 'https://attacker.example/'})
        self.assertNotEqual(self.client.session.get('active_role'), 'federation')
        self.assertEqual(self.client.get(reverse('competition_create')).status_code, 403)
        if response.status_code in (301, 302, 303):
            self.assertFalse(response.url.startswith('https://attacker.example'))

    def registration_data(self, **overrides):
        data = {
            'username': 'new-person', 'email': 'new@example.test', 'first_name': 'Тестовый',
            'last_name': 'Участник', 'password1': 'DifferentStrongPass2026!',
            'password2': 'DifferentStrongPass2026!', 'roles': ['athlete'],
            'birth_date': date(self.today.year - 20, 1, 1).isoformat(),
            'municipality': 'Оренбург', 'organization': self.organization.pk,
            'disciplines': [self.discipline.pk], 'start_year': self.today.year - 1,
            'personal_consent': 'on',
        }
        data.update(overrides)
        return data

    def test_registration_records_consent_without_allowing_federation(self):
        self.client.post(reverse('register'), self.registration_data(roles=['federation']))
        self.assertFalse(Profile.objects.filter(user__username='new-person', roles=['federation']).exists())
        self.assertFalse(User.objects.filter(username='new-person').exists())
        self.client.post(reverse('register'), self.registration_data())
        person = Profile.objects.get(user__username='new-person')
        self.assertEqual(person.roles, ['athlete'])
        self.assertTrue(Consent.objects.filter(user=person.user, kind='personal_data').exists())

    def test_minor_requires_representative_and_consent(self):
        birthday = date(self.today.year - 15, 1, 1).isoformat()
        self.client.post(reverse('register'), self.registration_data(birth_date=birthday))
        self.assertFalse(User.objects.filter(username='new-person').exists())
        self.client.post(reverse('register'), self.registration_data(
            birth_date=birthday, guardian_name='Вымышленный представитель', guardian_consent='on',
        ))
        person = Profile.objects.get(user__username='new-person')
        self.assertTrue(Consent.objects.filter(user=person.user, kind='guardian').exists())

    def test_applicant_cannot_impersonate_other_athlete(self):
        competition = self.competition()
        self.login_as(self.athlete)
        self.client.post(reverse('application_create', args=[competition.pk]), {
            'athlete': self.other.pk, 'discipline': self.discipline.pk,
        })
        self.assertFalse(Application.objects.filter(competition=competition).exists())

    def test_registration_enforces_discipline_age_and_deadline(self):
        competition = self.competition()
        self.login_as(self.athlete)
        self.client.post(reverse('application_create', args=[competition.pk]), {
            'athlete': self.athlete.pk, 'discipline': self.other_discipline.pk,
        })
        self.assertFalse(Application.objects.filter(competition=competition).exists())
        competition.age_max = 16
        competition.save()
        self.client.post(reverse('application_create', args=[competition.pk]), {
            'athlete': self.athlete.pk, 'discipline': self.discipline.pk,
        })
        self.assertFalse(Application.objects.filter(competition=competition).exists())
        competition.age_max = 99
        competition.registration_closes = self.today - timedelta(days=1)
        competition.save()
        self.client.post(reverse('application_create', args=[competition.pk]), {
            'athlete': self.athlete.pk, 'discipline': self.discipline.pk,
        })
        self.assertFalse(Application.objects.filter(competition=competition).exists())

    def test_result_rejects_pending_application_and_duplicate_placing(self):
        competition = self.competition(state='closed')
        pending = self.application(competition, status='pending')
        self.login_as(self.federation)
        self.client.post(reverse('result_save', args=[competition.pk]), {
            'application': pending.pk, 'place': 1, 'score': 100,
        })
        self.assertFalse(Result.objects.filter(application=pending).exists())
        pending.status = 'approved'
        pending.save()
        Result.objects.create(application=pending, place=1, score=100)
        second = self.application(competition, self.other)
        self.client.post(reverse('result_save', args=[competition.pk]), {
            'application': second.pk, 'place': 1, 'score': 50,
        })
        self.assertFalse(Result.objects.filter(application=second).exists())

    def test_incomplete_protocol_cannot_publish_and_published_results_are_immutable(self):
        competition = self.competition(state='closed')
        application = self.application(competition)
        self.login_as(self.federation)
        self.client.post(reverse('competition_action', args=[competition.pk]), {'action': 'publish'})
        competition.refresh_from_db()
        self.assertFalse(competition.protocol_published)
        result = Result.objects.create(application=application, place=1, score=100)
        self.client.post(reverse('competition_action', args=[competition.pk]), {'action': 'publish'})
        competition.refresh_from_db()
        self.assertTrue(competition.protocol_published)
        self.client.post(reverse('result_save', args=[competition.pk]), {
            'application': application.pk, 'place': 2, 'score': 0,
        })
        result.refresh_from_db()
        self.assertEqual(result.place, 1)
        self.assertEqual(result.score, Decimal('100'))

    def test_private_profile_and_exports_are_authorized(self):
        self.assertEqual(self.client.get(reverse('athlete_detail', args=[self.athlete.pk])).status_code, 302)
        self.login_as(self.other)
        self.assertIn(self.client.get(reverse('athlete_detail', args=[self.athlete.pk])).status_code, (403, 404))
        self.assertIn(self.client.get(reverse('export_document', args=['athlete', self.athlete.pk, 'xlsx'])).status_code, (403, 404))
        self.login_as(self.athlete)
        self.assertEqual(self.client.get(reverse('athlete_detail', args=[self.athlete.pk])).status_code, 200)

    def test_profile_update_cannot_assign_a_privileged_role(self):
        self.login_as(self.athlete)
        self.client.post(reverse('profile'), {
            'first_name': 'Обновлённый', 'last_name': 'Участник',
            'birth_date': self.athlete.birth_date.isoformat(), 'municipality': 'Оренбург',
            'organization': self.organization.pk, 'disciplines': [self.discipline.pk],
            'start_year': self.athlete.start_year, 'roles': ['athlete', 'federation'],
        })
        self.athlete.refresh_from_db()
        self.assertEqual(self.athlete.roles, ['athlete'])
        self.assertEqual(self.client.get(reverse('competition_create')).status_code, 403)

    def test_unpublished_result_cannot_back_rank_request(self):
        competition = self.competition(state='closed')
        result = Result.objects.create(application=self.application(competition), place=1, score=100)
        self.login_as(self.athlete)
        self.client.post(reverse('qualifications'), {
            'kind': 'rank', 'athlete': self.athlete.pk, 'result': result.pk,
            'requested_title': 'III разряд',
        })
        self.assertFalse(RankApplication.objects.exists())

    def test_rank_cannot_reference_another_athletes_result(self):
        result = self.result(self.other)
        self.login_as(self.athlete)
        self.client.post(reverse('qualifications'), {
            'kind': 'rank', 'athlete': self.athlete.pk, 'result': result.pk,
            'requested_title': 'III разряд',
        })
        self.assertFalse(RankApplication.objects.exists())

    def test_rank_approval_needs_order_and_valid_dates(self):
        result = self.result()
        qualification = RankApplication.objects.create(
            athlete=self.athlete, result=result, kind='rank', requested_title='III разряд',
            submitted_by=self.athlete.user,
        )
        self.login_as(self.federation)
        self.client.post(reverse('qualification_review', args=[qualification.pk]), {
            'status': 'approved', 'assigned_on': self.today.isoformat(),
            'valid_until': (self.today - timedelta(days=1)).isoformat(), 'order_number': '',
        })
        qualification.refresh_from_db()
        self.assertEqual(qualification.status, 'pending')

    def test_coach_requires_athlete_confirmed_discipline_link(self):
        competition = self.competition()
        link = CoachLink.objects.create(coach=self.coach, athlete=self.athlete, discipline=self.discipline)
        self.login_as(self.coach)
        payload = {'athlete': self.athlete.pk, 'discipline': self.discipline.pk}
        self.client.post(reverse('application_create', args=[competition.pk]), payload)
        self.assertFalse(Application.objects.exists())
        self.login_as(self.athlete)
        self.client.post(reverse('link_update', args=[link.pk]), {'status': 'active'})
        link.refresh_from_db()
        self.assertEqual(link.status, 'active')
        self.login_as(self.coach)
        self.client.post(reverse('application_create', args=[competition.pk]), payload)
        application = Application.objects.get(competition=competition)
        self.assertEqual(application.athlete, self.athlete)
        self.assertEqual(application.submitted_by, self.coach.user)

    def test_rating_uses_season_podium_multiplier_and_disqualification(self):
        from .services import calculate_rating
        self.result(self.athlete, place=1)
        self.result(self.athlete, place=2)
        self.result(self.other, place=1, level='russian_championship')
        previous = date(self.today.year - 1, 6, 1)
        self.result(self.athlete, place=1, level='russian_championship', when=previous)
        entries = calculate_rating(self.today.year, self.discipline.pk)
        entry = next(entry for entry in entries if entry['athlete'].pk == self.athlete.pk)
        self.assertEqual(Decimal(str(entry['points'])), Decimal('84'))
        self.assertEqual(Decimal(str(entry['coefficient'])), Decimal('1.2'))
        self.assertEqual(len(entry['breakdown']), 2)
        self.other.disqualified_until = self.today + timedelta(days=30)
        self.other.save()
        self.assertNotIn(self.other.pk, [entry['athlete'].pk for entry in calculate_rating(self.today.year, self.discipline.pk)])

    def test_rating_renders_published_breakdown_including_zero_points(self):
        winner = self.result(self.athlete, place=1)
        fourth = self.result(self.other, place=4)
        self.login_as(self.federation)
        response = self.client.get(reverse('rating'), {
            'season': self.today.year, 'discipline': self.discipline.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, winner.application.competition.title)
        self.assertContains(response, fourth.application.competition.title)
        self.assertContains(response, self.athlete.display_name)
        self.assertContains(response, self.other.display_name)
        self.assertContains(response, '+40')
        self.assertContains(response, '+0')

    def test_team_rejects_unranked_and_out_of_discipline_members(self):
        self.result(self.athlete)
        self.login_as(self.federation)
        payload = {
            'season': self.today.year, 'discipline': self.discipline.pk,
            'age_min': 12, 'age_max': 99, 'members': [self.other.pk],
        }
        self.client.post(reverse('team_create'), payload)
        self.assertFalse(Team.objects.exists())
        payload['members'] = [self.athlete.pk]
        self.client.post(reverse('team_create'), payload)
        team = Team.objects.get()
        self.assertEqual(list(team.members.all()), [self.athlete])

    def test_team_respects_minimum_rank_filter(self):
        lower = self.result(self.athlete)
        higher = self.result(self.other)
        for person, result, title in [(self.athlete, lower, 'III разряд'), (self.other, higher, 'I разряд')]:
            RankApplication.objects.create(
                athlete=person, result=result, requested_title=title, submitted_by=self.federation.user,
                status='approved', assigned_on=self.today, valid_until=self.today + timedelta(days=365), order_number='ДЕМО',
            )
        self.login_as(self.federation)
        payload = {
            'season': self.today.year, 'discipline': self.discipline.pk,
            'age_min': 12, 'age_max': 99, 'min_rank': 'I разряд', 'members': [self.athlete.pk],
        }
        self.client.post(reverse('team_create'), payload)
        self.assertFalse(Team.objects.exists())
        payload['members'] = [self.other.pk]
        self.client.post(reverse('team_create'), payload)
        self.assertEqual(list(Team.objects.get().members.all()), [self.other])

    def test_coach_cannot_confirm_own_link_request(self):
        link = CoachLink.objects.create(coach=self.coach, athlete=self.athlete, discipline=self.discipline)
        self.login_as(self.coach)
        self.client.post(reverse('link_update', args=[link.pk]), {'status': 'active'})
        link.refresh_from_db()
        self.assertEqual(link.status, 'pending')

    def test_results_from_other_discipline_do_not_inflate_rating(self):
        from .services import calculate_rating
        self.result(self.athlete)
        competition = self.competition(state='finished', protocol_published=True)
        competition.disciplines.add(self.other_discipline)
        self.athlete.disciplines.add(self.other_discipline)
        application = self.application(competition, discipline=self.other_discipline)
        Result.objects.create(application=application, place=1, score=90)
        entries = calculate_rating(self.today.year, self.discipline.pk)
        self.assertEqual(len(entries), 1)
        self.assertEqual(Decimal(str(entries[0]['points'])), Decimal('40'))
        self.assertEqual(Decimal(str(entries[0]['coefficient'])), Decimal('1'))

    def test_census_uses_membership_years_and_organization(self):
        from .services import census
        self.other.end_year = self.today.year - 1
        self.other.status = 'archived'
        self.other.save()
        counts = census(self.today.year - 1, self.today.year, organization=self.organization.pk)
        by_year = {entry['year']: entry['total'] for entry in counts}
        self.assertEqual(by_year[self.today.year - 1] - by_year[self.today.year], 1)

    def test_csrf_is_required_for_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.federation.user)
        response = client.post(reverse('switch_role'), {'role': 'athlete'})
        self.assertEqual(response.status_code, 403)

    def test_notifications_cannot_mark_another_users_messages(self):
        other = Notification.objects.create(user=self.other.user, title='Чужое', body='Личное')
        self.login_as(self.athlete)
        self.client.post(reverse('notifications'), {'id': other.pk})
        other.refresh_from_db()
        self.assertFalse(other.read)

    def test_key_pages_render_for_each_role(self):
        for person in [self.federation, self.athlete, self.coach, self.judge]:
            self.login_as(person)
            for name in ['dashboard', 'competitions', 'qualifications', 'rating', 'notifications', 'profile', 'guide']:
                with self.subTest(role=person.roles[0], page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)
