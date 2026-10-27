from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from federation.models import Application, Competition, Discipline, Profile, Result
from governance.models import AwardRule, ViolationRecord
from governance.services import award_candidates, valid_results
from .models import ParticipationCertificate, RankCriterion, result_fingerprint
from .services import rank_candidates


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SanctionEligibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.day = timezone.localdate()
        cls.discipline = Discipline.objects.create(name='Аудит алгоритмов', code='sanction-algorithms')
        cls.user = get_user_model().objects.create_user('sanction-athlete', first_name='Аудит', last_name='Спортсмен')
        cls.person = Profile.objects.create(user=cls.user, roles=['athlete'], birth_date=date(2000, 1, 1), start_year=2020)
        cls.person.disciplines.add(cls.discipline)
        cls.competition = cls.make_competition('Основной старт')
        application = Application.objects.create(competition=cls.competition, athlete=cls.person,
            discipline=cls.discipline, status='approved', submitted_by=cls.user)
        cls.result = Result.objects.create(application=application, place=1, score=100)
        RankCriterion.objects.create(discipline=cls.discipline, rank_title='III разряд', levels=['regional'],
            max_place=3, min_score=0, age_min=0, age_max=120, source_text='Демонстрационный критерий')
        AwardRule.objects.create(level='regional', place=1, amount=100)
        cls.certificate = ParticipationCertificate.objects.create(result=cls.result, issued_by=cls.user,
            public_consent=True, fingerprint=result_fingerprint(cls.result), snapshot={'athlete_name': cls.person.display_name})

    @classmethod
    def make_competition(cls, title):
        competition = Competition.objects.create(title=title, starts_on=cls.day, ends_on=cls.day,
            registration_opens=cls.day, registration_closes=cls.day, location='Демонстрация',
            state='finished', protocol_published=True)
        competition.disciplines.add(cls.discipline)
        return competition

    def violation(self, *, decision='technical_defeat', starts_on=None, ends_on=None, competition=None):
        return ViolationRecord.objects.create(athlete=self.person, competition=competition or self.competition,
            decision=decision, starts_on=starts_on or self.day, ends_on=ends_on,
            reason='Проверенное демонстрационное решение', recorded_by=self.user)

    def test_technical_defeat_removes_candidates_awards_and_public_validity(self):
        self.assertEqual(len(rank_candidates()), 1)
        self.assertEqual(len(award_candidates(self.day.year)), 1)
        self.assertTrue(self.certificate.valid)
        self.violation()
        self.assertEqual(rank_candidates(), [])
        self.assertEqual(award_candidates(self.day.year), [])
        self.assertFalse(self.certificate.valid)
        response = self.client.get(reverse('development:verify', args=[self.certificate.number]))
        self.assertContains(response, 'НЕДЕЙСТВИТЕЛЬНА / ОТОЗВАНА')

    def test_effective_date_controls_rank_awards_and_certificate(self):
        tomorrow = self.day + timedelta(days=1)
        self.violation(decision='disqualification', starts_on=tomorrow, ends_on=tomorrow + timedelta(days=7))
        self.assertEqual(len(rank_candidates(on_date=self.day)), 1)
        self.assertEqual(len(award_candidates(self.day.year, on_date=self.day)), 1)
        self.assertTrue(self.certificate.valid)
        self.assertEqual(rank_candidates(on_date=tomorrow), [])
        self.assertEqual(award_candidates(self.day.year, on_date=tomorrow), [])
        with patch('governance.services.timezone.localdate', return_value=tomorrow):
            self.assertFalse(self.certificate.valid)

    def test_overturned_decision_restores_source_validity(self):
        decision = self.violation()
        self.assertFalse(self.certificate.valid)
        decision.status = 'overturned'
        decision.save(update_fields=['status'])
        self.assertEqual(len(rank_candidates()), 1)
        self.assertEqual(len(award_candidates(self.day.year)), 1)
        self.assertTrue(self.certificate.valid)

    def test_global_active_suspension_excludes_current_candidates_on_another_competition(self):
        other_competition = self.make_competition('Другой старт')
        decision = self.violation(decision='disqualification', competition=other_competition,
            starts_on=self.day, ends_on=self.day + timedelta(days=7))
        self.assertEqual(rank_candidates(), [])
        self.assertFalse(valid_results(Result.objects.all(), exclude_active_disqualified=True).exists())
        # An unrelated later ban does not erase an authentic historical result.
        self.assertTrue(self.certificate.valid)
        self.assertEqual(len(award_candidates(self.day.year)), 1)
        decision.status = 'overturned'
        decision.save(update_fields=['status'])
        self.assertEqual(len(rank_candidates()), 1)

    def test_manual_profile_suspension_also_excludes_current_candidates(self):
        self.person.disqualified_until = self.day + timedelta(days=7)
        self.person.save(update_fields=['disqualified_until'])
        self.assertEqual(rank_candidates(), [])
        self.assertEqual(len(rank_candidates(on_date=self.day + timedelta(days=8))), 1)

    def test_expiring_suspension_does_not_restore_voided_competition_result(self):
        self.violation(decision='disqualification', starts_on=self.day - timedelta(days=10), ends_on=self.day - timedelta(days=1))
        self.assertEqual(rank_candidates(), [])
        self.assertEqual(award_candidates(self.day.year), [])
        self.assertFalse(self.certificate.valid)

    def test_new_certificate_cannot_be_issued_for_technical_defeat_and_history_remains(self):
        self.violation()
        self.client.force_login(self.user)
        response = self.client.post(reverse('development:certificate_issue'), {'result': self.result.pk, 'public_consent': 'on'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ParticipationCertificate.objects.count(), 1)
        response = self.client.get(reverse('development:certificates'))
        self.assertContains(response, str(self.certificate.number))
        self.assertContains(response, 'Недействительна')

    def test_rank_submission_rejects_sanctioned_source(self):
        self.violation()
        self.client.force_login(self.user)
        response = self.client.post(reverse('qualifications'), {'kind':'rank','athlete':self.person.pk,'result':self.result.pk,'requested_title':'III разряд'})
        self.assertEqual(response.status_code, 400)
        from federation.models import RankApplication
        self.assertFalse(RankApplication.objects.exists())

    def test_pending_rank_cannot_be_approved_after_source_sanction(self):
        from federation.models import RankApplication
        request = RankApplication.objects.create(athlete=self.person, result=self.result, kind='rank', requested_title='III разряд', submitted_by=self.user)
        self.violation()
        reviewer = get_user_model().objects.create_user('reviewer')
        Profile.objects.create(user=reviewer, roles=['federation'])
        self.client.force_login(reviewer)
        response = self.client.post(reverse('qualification_review', args=[request.pk]), {'status':'approved','assigned_on':self.day.isoformat(),'valid_until':(self.day+timedelta(days=365)).isoformat(),'order_number':'Демо-1'})
        self.assertEqual(response.status_code, 400)
        request.refresh_from_db()
        self.assertEqual(request.status, 'pending')
