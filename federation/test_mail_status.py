import socket
from aiosmtpd.controller import Controller
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .mail_status import TEST_SUBJECT
from .models import AuditLog, Notification, Profile


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MailStatusTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.fed = user_model.objects.create_user('mail-federation', email='federation@example.test', password='FspDemo2026!')
        Profile.objects.create(user=self.fed, roles=['federation'])
        self.other = user_model.objects.create_user('mail-athlete', email='athlete@example.test', password='FspDemo2026!')
        Profile.objects.create(user=self.other, roles=['athlete'])

    def test_non_federation_cannot_inspect_queue_or_send_test(self):
        self.client.force_login(self.other)
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)(reverse('mail_status')).status_code, 403)
        self.assertFalse(Notification.objects.exists())

    def test_unconfigured_channel_does_not_queue_a_fake_external_test(self):
        self.client.force_login(self.fed)
        self.assertContains(self.client.get(reverse('mail_status')), 'Внешняя почта не настроена')
        self.assertEqual(self.client.post(reverse('mail_status')).status_code, 302)
        self.assertFalse(Notification.objects.filter(title=TEST_SUBJECT).exists())

    def test_diagnostics_never_reveal_another_users_test_body(self):
        Notification.objects.create(user=self.other, title=TEST_SUBJECT, body='PRIVATE-OTHER-MESSAGE')
        self.client.force_login(self.fed)
        response = self.client.get(reverse('mail_status'))
        self.assertNotContains(response, 'PRIVATE-OTHER-MESSAGE')
        self.assertNotContains(response, self.other.email)
        self.assertContains(response, 'Тестов пока нет.')

    def test_explicit_action_uses_real_smtp_only_for_current_users_address(self):
        received = []

        class Handler:
            async def handle_DATA(self, server, session, envelope):
                received.append(envelope)
                return '250 accepted for local integration test'

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        controller = Controller(Handler(), hostname='127.0.0.1', port=port)
        controller.start()
        try:
            with override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='127.0.0.1',
                                   EMAIL_PORT=port, EMAIL_USE_TLS=False, EMAIL_USE_SSL=False, EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD=''):
                self.client.force_login(self.fed)
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse('mail_status'), {'recipient': self.other.email})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0].rcpt_tos, [self.fed.email])
                item = Notification.objects.get(title=TEST_SUBJECT)
                self.assertEqual(item.user, self.fed)
                self.assertEqual(item.email_delivery_status, 'sent')
                self.assertTrue(AuditLog.objects.filter(user=self.fed, action='Проверка почтовых уведомлений').exists())
                self.assertContains(self.client.get(reverse('mail_status')), 'Принято почтовым сервером')
        finally:
            controller.stop()
