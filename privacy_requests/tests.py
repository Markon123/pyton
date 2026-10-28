import json
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from federation.models import Profile
from .models import PrivacyRequest


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PersonalDataBoundaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('privacy-owner', password='Private!Test827Strong', email='owner@example.test')
        Profile.objects.create(user=self.user, roles=['athlete'])
        self.other = get_user_model().objects.create_user('privacy-other', email='other-private@example.test')
        Profile.objects.create(user=self.other, roles=['athlete'])
        self.fed = get_user_model().objects.create_user('privacy-federation')
        Profile.objects.create(user=self.fed, roles=['federation'])

    def test_export_is_own_data_and_excludes_password_and_other_person(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('privacy_requests:export'), {'user_id': self.other.pk})
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        json.loads(text)
        self.assertIn(self.user.email, text)
        self.assertNotIn(self.other.email, text)
        self.assertNotIn(self.user.password, text)
        self.assertNotIn('password', text)
        self.assertIn('no-store', response['Cache-Control'])

    def test_request_is_idempotent_and_another_athlete_cannot_read_or_review(self):
        self.client.force_login(self.user)
        self.client.post(reverse('privacy_requests:index'), {'reason': 'Прошу рассмотреть удаление.'})
        self.client.post(reverse('privacy_requests:index'), {'reason': 'Повторное обращение.'})
        record = PrivacyRequest.objects.get()
        self.assertEqual(record.user, self.user)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('privacy_requests:detail', args=[record.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('privacy_requests:review', args=[record.pk]), {'status': 'approved', 'decision_reason': 'Нет прав'}).status_code, 403)

    def test_review_needs_reason_and_approval_does_not_pretend_records_were_erased(self):
        record = PrivacyRequest.objects.create(user=self.user, reason='Обращение')
        self.client.force_login(self.fed)
        url = reverse('privacy_requests:review', args=[record.pk])
        self.assertEqual(self.client.post(url, {'status': 'approved', 'decision_reason': ''}).status_code, 400)
        self.assertEqual(self.client.post(url, {'status': 'approved', 'decision_reason': 'Запрос согласован; требуется исполнение по политике хранения.'}).status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'approved')
        self.assertTrue(get_user_model().objects.get(pk=self.user.pk).is_active)
        self.assertTrue(record.events.exists())
