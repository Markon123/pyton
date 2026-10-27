import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from federation.models import Application, CoachLink, Competition, Discipline, Notification, Profile, Result
from .models import Appeal, AwardPetition, AwardRule, ProtocolAmendment, ViolationRecord
from .services import active_disqualifications, award_candidates


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class GovernanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.day = timezone.localdate()
        cls.discipline = Discipline.objects.create(name='Демо алгоритмы', code='gov-alg')
        cls.other_disc = Discipline.objects.create(name='Демо продукты', code='gov-product')
        cls.people = {}
        for username, roles in [('fed', ['federation', 'athlete']), ('athlete', ['athlete']), ('other', ['athlete']), ('coach', ['coach']), ('ministry', ['ministry'])]:
            user = get_user_model().objects.create_user(username='gov-' + username, password='Testing2026!', first_name=username, email=username + '@example.test')
            person = Profile.objects.create(user=user, roles=roles, birth_date=date(2000, 1, 1), start_year=2020)
            person.disciplines.set([cls.discipline, cls.other_disc])
            cls.people[username] = person
        cls.comp = Competition.objects.create(title='Демо протокол', starts_on=cls.day, ends_on=cls.day,
            registration_opens=cls.day - timedelta(days=5), registration_closes=cls.day,
            location='Демо', state='finished', protocol_published=True)
        cls.comp.disciplines.set([cls.discipline, cls.other_disc])
        for key, place in [('athlete', 1), ('other', 2)]:
            app = Application.objects.create(competition=cls.comp, athlete=cls.people[key], discipline=cls.discipline,
                submitted_by=cls.people[key].user, status='approved')
            result = Result.objects.create(application=app, place=place, score=100 - place)
            setattr(cls, key + '_result', result)
        cls.rule = AwardRule.objects.create(level='regional', place=1, amount=Decimal('10000.00'))
        AwardRule.objects.create(level='regional', place=2, amount=Decimal('5000.00'))

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)

    def login(self, who, role=None):
        person = self.people[who]
        self.client.force_login(person.user)
        session = self.client.session
        session['active_role'] = role or person.roles[0]
        session.save()

    def create_appeal(self):
        return Appeal.objects.create(result=self.athlete_result, submitted_by=self.people['athlete'].user, reason='Прошу проверить баллы')

    def test_published_protocol_correction_keeps_snapshot_and_history(self):
        self.login('athlete')
        response = self.client.post(reverse('governance:appeals'), {'result': self.athlete_result.pk, 'reason': 'Доказана ошибка счёта',
            'evidence_file': SimpleUploadedFile('proof.pdf', b'%PDF-1.4\n demo', content_type='application/pdf')})
        self.assertEqual(response.status_code, 302)
        appeal = Appeal.objects.get()
        self.login('fed')
        response = self.client.post(reverse('governance:appeal_detail', args=[appeal.pk]), {'status': 'upheld', 'reason': 'Ошибка подтверждена, исправить баллы', 'score': '125.50'})
        self.assertEqual(response.status_code, 302)
        self.athlete_result.refresh_from_db()
        self.comp.refresh_from_db()
        self.assertEqual(self.athlete_result.score, Decimal('125.50'))
        self.assertTrue(self.comp.protocol_published)
        change = ProtocolAmendment.objects.get()
        self.assertEqual(change.old_value['score'], '99.00')
        self.assertEqual(change.new_value['score'], '125.50')
        self.assertTrue(Notification.objects.filter(user=self.people['athlete'].user, title='Решение по апелляции').exists())

    def test_duplicate_place_amendment_is_rejected_atomically(self):
        appeal = self.create_appeal()
        self.login('fed')
        response = self.client.post(reverse('governance:appeal_detail', args=[appeal.pk]), {'status': 'upheld', 'reason': 'Корректировка места', 'place': 2, 'score': '500'})
        self.assertEqual(response.status_code, 400)
        self.athlete_result.refresh_from_db()
        appeal.refresh_from_db()
        self.assertEqual(self.athlete_result.place, 1)
        self.assertEqual(appeal.status, 'pending')
        self.assertFalse(ProtocolAmendment.objects.exists())

    def test_other_athlete_cannot_forge_source_or_read_evidence(self):
        appeal = self.create_appeal()
        self.login('other')
        response = self.client.post(reverse('governance:appeals'), {'result': self.athlete_result.pk, 'reason': 'Подмена спортсмена'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Appeal.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('governance:appeal_detail', args=[appeal.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('governance:evidence', args=['appeal', appeal.pk])).status_code, 404)

    def test_coach_requires_confirmed_matching_discipline(self):
        self.login('coach')
        link = CoachLink.objects.create(coach=self.people['coach'], athlete=self.people['athlete'], discipline=self.other_disc, status='active')
        response = self.client.post(reverse('governance:appeals'), {'result': self.athlete_result.pk, 'reason': 'Не моя дисциплина'})
        self.assertEqual(response.status_code, 400)
        appeal = self.create_appeal()
        self.assertEqual(self.client.get(reverse('governance:appeal_detail', args=[appeal.pk])).status_code, 404)
        link.discipline = self.discipline
        link.save()
        response = self.client.post(reverse('governance:appeals'), {'result': self.athlete_result.pk, 'reason': 'Проверить баллы подопечного'})
        self.assertEqual(response.status_code, 302)

    def test_coach_cannot_appeal_violation_from_unrelated_competition_discipline(self):
        comp = Competition.objects.create(title='Только продукты', starts_on=self.day, ends_on=self.day,
            registration_opens=self.day, registration_closes=self.day, location='Демо', state='finished', protocol_published=True)
        comp.disciplines.add(self.other_disc)
        violation = ViolationRecord.objects.create(athlete=self.people['athlete'], competition=comp,
            reason='Предупреждение по продуктовой дисциплине', decision='warning', recorded_by=self.people['fed'].user)
        CoachLink.objects.create(coach=self.people['coach'], athlete=self.people['athlete'], discipline=self.discipline, status='active')
        self.login('coach')
        self.assertEqual(self.client.post(reverse('governance:appeals'), {'violation': violation.pk, 'reason': 'Чужая дисциплина'}).status_code, 400)
        self.assertFalse(Appeal.objects.exists())

    def test_other_athlete_amendment_reason_is_not_exposed(self):
        appeal = self.create_appeal()
        other = Appeal.objects.create(result=self.other_result, submitted_by=self.people['other'].user, reason='Частное обращение')
        ProtocolAmendment.objects.create(competition=self.comp, result=self.other_result, appeal=other,
            old_value={'place': 2, 'score': '98'}, new_value={'place': 2, 'score': '99'},
            reason='Конфиденциальная подробность другого спортсмена', amended_by=self.people['fed'].user)
        self.login('athlete')
        response = self.client.get(reverse('governance:appeal_detail', args=[appeal.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Конфиденциальная подробность другого спортсмена')
        self.login('fed')
        self.assertContains(self.client.get(reverse('governance:appeal_detail', args=[appeal.pk])), 'Конфиденциальная подробность другого спортсмена')

    def test_athlete_role_of_federation_account_cannot_review(self):
        appeal = self.create_appeal()
        self.login('fed', role='athlete')
        response = self.client.post(reverse('governance:appeal_detail', args=[appeal.pk]), {'status': 'upheld', 'reason': 'Попытка получить права', 'score': '999'})
        self.assertIn(response.status_code, (403, 404))
        self.assertFalse(ProtocolAmendment.objects.exists())

    def test_review_requires_reason_and_final_decision_is_immutable(self):
        appeal = self.create_appeal()
        self.login('fed')
        url = reverse('governance:appeal_detail', args=[appeal.pk])
        self.assertEqual(self.client.post(url, {'status': 'rejected', 'reason': ''}).status_code, 400)
        self.assertEqual(self.client.post(url, {'status': 'rejected', 'reason': 'Доказательств недостаточно'}).status_code, 302)
        self.assertEqual(self.client.post(url, {'status': 'upheld', 'reason': 'Повторное изменение'}).status_code, 400)

    def test_evidence_rejects_disguised_html(self):
        self.login('athlete')
        response = self.client.post(reverse('governance:appeals'), {'result': self.athlete_result.pk, 'reason': 'Файл',
            'evidence_file': SimpleUploadedFile('image.png', b'<html>not image</html>', content_type='image/png')})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Appeal.objects.exists())

    def test_future_disqualification_uses_effective_dates(self):
        self.login('fed')
        tomorrow = self.day + timedelta(days=1)
        end = self.day + timedelta(days=30)
        response = self.client.post(reverse('governance:violation_create'), {'athlete': self.people['athlete'].pk, 'competition': self.comp.pk,
            'reason': 'Решение действует с завтрашнего дня', 'decision': 'disqualification', 'starts_on': tomorrow, 'ends_on': end})
        self.assertEqual(response.status_code, 302)
        self.people['athlete'].refresh_from_db()
        self.assertIsNone(self.people['athlete'].disqualified_until)
        self.assertFalse(active_disqualifications(self.day).exists())
        self.assertTrue(active_disqualifications(tomorrow).exists())
        from federation.services import calculate_rating
        self.assertIn(self.people['athlete'].pk, [e['athlete'].pk for e in calculate_rating(self.day.year, self.discipline.pk)])

    def test_technical_defeat_removes_only_that_competition_result(self):
        from federation.services import calculate_rating
        before = calculate_rating(self.day.year, self.discipline.pk)
        self.assertIn(self.people['athlete'].pk, [e['athlete'].pk for e in before])
        ViolationRecord.objects.create(athlete=self.people['athlete'], competition=self.comp, reason='Подтверждённое техническое поражение',
            decision='technical_defeat', recorded_by=self.people['fed'].user)
        after = calculate_rating(self.day.year, self.discipline.pk)
        self.assertNotIn(self.people['athlete'].pk, [e['athlete'].pk for e in after])
        self.assertIn(self.people['other'].pk, [e['athlete'].pk for e in after])

    def test_successful_violation_appeal_restores_eligibility(self):
        self.login('fed')
        end = self.day + timedelta(days=30)
        response = self.client.post(reverse('governance:violation_create'), {'athlete': self.people['athlete'].pk, 'competition': self.comp.pk,
            'reason': 'Решение о дисквалификации', 'decision': 'disqualification', 'starts_on': self.day, 'ends_on': end})
        self.assertEqual(response.status_code, 302)
        violation = ViolationRecord.objects.get()
        self.login('athlete')
        self.assertEqual(self.client.post(reverse('governance:appeals'), {'violation': violation.pk, 'reason': 'Прошу отменить на основании доказательств'}).status_code, 302)
        appeal = Appeal.objects.get()
        self.login('fed')
        self.assertEqual(self.client.post(reverse('governance:appeal_detail', args=[appeal.pk]), {'status': 'upheld', 'reason': 'Нарушение не подтвердилось'}).status_code, 302)
        violation.refresh_from_db()
        self.people['athlete'].refresh_from_db()
        self.assertEqual(violation.status, 'overturned')
        self.assertIsNone(self.people['athlete'].disqualified_until)
        self.assertFalse(active_disqualifications().exists())

    def test_award_uses_best_result_and_persists_calculation_snapshot(self):
        second = Competition.objects.create(title='Второй старт', starts_on=self.day, ends_on=self.day, registration_opens=self.day, registration_closes=self.day, location='Демо', level='interregional', state='finished', protocol_published=True)
        second.disciplines.add(self.discipline)
        app = Application.objects.create(competition=second, athlete=self.people['athlete'], submitted_by=self.people['athlete'].user, discipline=self.discipline, status='approved')
        Result.objects.create(application=app, place=1, score=500)
        AwardRule.objects.create(level='interregional', place=1, amount=Decimal('25000.00'))
        candidate = next(c for c in award_candidates(self.day.year) if c['athlete'].pk == self.people['athlete'].pk)
        self.assertEqual(candidate['amount'], Decimal('25000.00'))
        self.assertEqual(len(candidate['breakdown']), 2)
        self.login('fed')
        response = self.client.post(reverse('governance:petition_prepare'), {'year': self.day.year, 'athlete': self.people['athlete'].pk})
        self.assertEqual(response.status_code, 302)
        petition = AwardPetition.objects.get()
        self.assertEqual(petition.amount, Decimal('25000.00'))
        self.assertEqual(petition.provenance['policy'], 'best_result')
        self.assertEqual(petition.deadline, date(self.day.year + 1, 2, 1))
        self.rule.amount = 12345
        self.rule.save()
        petition.refresh_from_db()
        self.assertEqual(petition.amount, Decimal('25000.00'))
        self.client.post(reverse('governance:petition_prepare'), {'year': self.day.year, 'athlete': self.people['athlete'].pk})
        self.assertEqual(AwardPetition.objects.count(), 1)

    def test_petition_transition_order_and_masks_are_enforced(self):
        self.login('fed')
        self.client.post(reverse('governance:petition_prepare'), {'year': self.day.year, 'athlete': self.people['athlete'].pk})
        petition = AwardPetition.objects.get()
        url = reverse('governance:petition_detail', args=[petition.pk])
        masks = {'passport_mask': '** ** ******', 'inn_mask': '************', 'snils_mask': '***-***-*** **', 'account_mask': '********************'}
        self.assertEqual(self.client.post(url, {'status': 'awarded', **masks}).status_code, 400)
        self.assertEqual(self.client.post(url, {'status': 'sent', **masks, 'inn_mask': '123456789012'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'status': 'sent', **masks}).status_code, 302)
        self.assertEqual(self.client.post(url, {'status': 'awarded', **masks}).status_code, 302)
        self.assertEqual(self.client.post(url, {'status': 'prepared', **masks}).status_code, 400)

    def test_ministry_read_only_no_private_cards(self):
        self.login('ministry')
        response = self.client.get(reverse('governance:ministry'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '2000-01-01')
        self.assertEqual(self.client.post(reverse('governance:ministry'), {'year': self.day.year}).status_code, 403)
        self.assertEqual(self.client.post(reverse('governance:violation_create'), {'athlete': self.people['athlete'].pk}).status_code, 403)
        self.assertEqual(self.client.post(reverse('governance:petition_prepare'), {'year': self.day.year, 'athlete': self.people['athlete'].pk}).status_code, 403)

    def test_award_pdf_and_other_athlete_privacy(self):
        self.login('fed')
        self.client.post(reverse('governance:petition_prepare'), {'year': self.day.year, 'athlete': self.people['athlete'].pk})
        petition = AwardPetition.objects.get()
        url = reverse('governance:petition_document', args=[petition.pk, 'pdf'])
        self.assertTrue(self.client.get(url).content.startswith(b'%PDF'))
        self.login('other')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_award_reminder_is_deadline_bound_and_idempotent(self):
        from django.core.management import call_command
        with patch('governance.management.commands.award_reminders.timezone.localdate', return_value=date(2030, 1, 15)):
            call_command('award_reminders')
            call_command('award_reminders')
        self.assertEqual(Notification.objects.filter(title__contains='до 1 февраля 2030').count(), 1)
        with patch('governance.management.commands.award_reminders.timezone.localdate', return_value=date(2031, 5, 1)):
            call_command('award_reminders')
        self.assertFalse(Notification.objects.filter(title__contains='до 1 февраля 2031').exists())
