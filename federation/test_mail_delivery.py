import io
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from threading import Event
from unittest.mock import patch

from aiosmtpd.controller import Controller
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .mail_delivery import MAX_ATTEMPTS, deliver_notification
from .models import Notification
from .services import notify


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MailDeliveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('maildemo', email='recipient@example.test', password='FspDemo2026!')

    def notice(self, **kwargs):
        return Notification.objects.create(user=self.user, title='Статус заявки', body='Ваша вымышленная заявка одобрена.', **kwargs)

    def test_local_backend_does_not_claim_external_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            with override_settings(EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend', EMAIL_FILE_PATH=folder):
                item = self.notice()
                self.assertEqual(deliver_notification(item.pk), 'local')
                item.refresh_from_db()
                self.assertTrue(item.email_sent)
                self.assertEqual(item.email_delivery_status, 'local')
                self.assertEqual(len(list(Path(folder).iterdir())), 1)
                call_command('process_outbox', stdout=io.StringIO())
                self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_real_smtp_transport_receives_recipient_and_russian_message(self):
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
                with self.captureOnCommitCallbacks(execute=True):
                    item = notify(self.user, 'Протокол опубликован', 'Результат: первое место. Данные вымышлены.')
                item.refresh_from_db()
                self.assertEqual(item.email_delivery_status, 'sent')
                self.assertTrue(item.email_sent)
                self.assertEqual(item.email_attempts, 1)
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0].rcpt_tos, ['recipient@example.test'])
                message = BytesParser(policy=policy.default).parsebytes(received[0].content)
                self.assertEqual(str(message['Subject']), 'Протокол опубликован')
                self.assertIn('первое место', message.get_content())
                self.assertEqual(deliver_notification(item.pk, force=True), 'sent')
                self.assertEqual(len(received), 1)
        finally:
            controller.stop()

    def test_failure_is_persisted_and_retried_only_when_due(self):
        item = self.notice()
        with patch('federation.mail_delivery.send_mail', side_effect=ConnectionRefusedError('sensitive-data-not-for-log')):
            self.assertEqual(deliver_notification(item.pk), 'failed')
        item.refresh_from_db()
        self.assertFalse(item.email_sent)
        self.assertEqual(item.email_attempts, 1)
        self.assertGreater(item.email_next_retry_at, timezone.now())
        self.assertNotIn('sensitive-data', item.email_last_error)
        with patch('federation.mail_delivery.send_mail', return_value=1) as send:
            call_command('process_outbox', stdout=io.StringIO())
            send.assert_not_called()
            Notification.objects.filter(pk=item.pk).update(email_next_retry_at=timezone.now() - timedelta(seconds=1))
            call_command('process_outbox', stdout=io.StringIO())
            send.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.email_attempts, 2)
        self.assertEqual(item.email_delivery_status, 'local')

    def test_claim_lease_prevents_duplicate_worker_delivery(self):
        item = self.notice(email_delivery_status='processing', email_attempts=1,
                           email_next_retry_at=timezone.now() + timedelta(minutes=2))
        with patch('federation.mail_delivery.send_mail', return_value=1) as send:
            self.assertEqual(deliver_notification(item.pk), 'processing')
            send.assert_not_called()
            Notification.objects.filter(pk=item.pk).update(email_next_retry_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(deliver_notification(item.pk), 'local')
            send.assert_called_once()

    def test_retry_limit_and_missing_email_are_explicit(self):
        item = self.notice(email_delivery_status='failed', email_attempts=MAX_ATTEMPTS, email_next_retry_at=None)
        with patch('federation.mail_delivery.send_mail') as send:
            call_command('process_outbox', stdout=io.StringIO())
            send.assert_not_called()
        with patch('federation.mail_delivery.send_mail', return_value=1) as send:
            call_command('process_outbox', retry_failed=True, stdout=io.StringIO())
            send.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.email_delivery_status, 'local')
        self.user.email = ''
        self.user.save()
        no_address = self.notice()
        self.assertEqual(deliver_notification(no_address.pk), 'skipped')

    def test_scheduler_persists_daily_run_and_processes_queues_each_cycle(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(LOCAL_DIR=Path(folder)):
            available = {name: 'test' for name in ['send_reminders', 'award_reminders', 'process_outbox', 'deliver_webhooks']}
            with patch('federation.management.commands.run_scheduler.get_commands', return_value=available), patch('federation.management.commands.run_scheduler.call_command') as invoke:
                call_command('run_scheduler', once=True, stdout=io.StringIO())
                call_command('run_scheduler', once=True, stdout=io.StringIO())
                names = [call.args[0] for call in invoke.call_args_list]
                self.assertEqual(names.count('send_reminders'), 1)
                self.assertEqual(names.count('award_reminders'), 1)
                self.assertEqual(names.count('process_outbox'), 2)
                self.assertEqual(names.count('deliver_webhooks'), 2)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ConcurrentMailDeliveryTests(TransactionTestCase):
    def test_two_workers_do_not_send_the_same_unexpired_claim(self):
        user = get_user_model().objects.create_user('claimdemo', email='claim@example.test')
        item = Notification.objects.create(user=user, title='Тест аренды', body='Вымышленные данные.')
        sending, release = Event(), Event()

        def transport(*args, **kwargs):
            sending.set()
            if not release.wait(5):
                raise RuntimeError('Test worker timed out')
            return 1

        def worker():
            close_old_connections()
            try:
                return deliver_notification(item.pk)
            finally:
                # This pool thread terminates after the test. A healthy
                # persistent PostgreSQL connection must still be closed here.
                connections['default'].close()

        with patch('federation.mail_delivery.send_mail', side_effect=transport) as send, ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(sending.wait(5), 'Первый обработчик не получил задачу.')
                self.assertEqual(deliver_notification(item.pk), 'processing')
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5), 'local')
            send.assert_called_once()
        item.refresh_from_db()
        self.assertEqual(item.email_attempts, 1)
