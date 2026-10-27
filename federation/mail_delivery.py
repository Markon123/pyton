"""Durable SMTP outbox with bounded retries and a short database claim lease."""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F, Q
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
LEASE_SECONDS = 120


def due_notifications(now=None, include_local=False):
    now = now or timezone.now()
    due = Q(email_delivery_status='queued') & (Q(email_next_retry_at__isnull=True) | Q(email_next_retry_at__lte=now))
    due |= Q(email_delivery_status='failed', email_attempts__lt=MAX_ATTEMPTS, email_next_retry_at__lte=now)
    due |= Q(email_delivery_status='processing', email_next_retry_at__lte=now)
    if include_local:
        due |= Q(email_delivery_status='local')
    return Notification.objects.filter(due).order_by('created_at', 'pk')


def deliver_notification(notification_id, force=False):
    """Return the delivery state. A successful SMTP response means server acceptance.

    Claims use a compare-and-set UPDATE rather than holding a transaction across
    network I/O. A crashed claim becomes retryable after its lease. Delivery is
    at least once: a crash between SMTP acceptance and DB acknowledgement may
    cause a duplicate, which no SMTP-only outbox can eliminate reliably.
    """
    now = timezone.now()
    eligible = due_notifications(now, include_local=force).filter(pk=notification_id)
    if force:
        eligible = Notification.objects.filter(pk=notification_id).filter(
            ~Q(email_delivery_status__in=['sent', 'processing']) |
            Q(email_delivery_status='processing', email_next_retry_at__lte=now))
    claimed = eligible.update(email_delivery_status='processing', email_attempts=F('email_attempts') + 1,
                              email_next_retry_at=now + timedelta(seconds=LEASE_SECONDS), email_last_error='')
    if not claimed:
        return Notification.objects.filter(pk=notification_id).values_list('email_delivery_status', flat=True).first()
    notice = Notification.objects.select_related('user').get(pk=notification_id)
    if not notice.user.email:
        Notification.objects.filter(pk=notification_id, email_delivery_status='processing').update(
            email_delivery_status='skipped', email_sent=False, email_next_retry_at=None,
            email_last_error='У пользователя не указан email.')
        return 'skipped'
    try:
        accepted = send_mail(notice.title, notice.body, settings.DEFAULT_FROM_EMAIL,
                             [notice.user.email], fail_silently=False)
        if accepted != 1:
            raise RuntimeError('Mail backend did not accept the message')
        status = 'sent' if settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend' else 'local'
        Notification.objects.filter(pk=notification_id, email_delivery_status='processing').update(
            email_delivery_status=status, email_sent=True, email_next_retry_at=None, email_last_error='')
        return status
    except Exception as error:
        # SMTP exception messages may include addresses or server configuration.
        # Persist only a safe exception class and generic explanation.
        message = f'{type(error).__name__}: почтовый транспорт не принял сообщение.'
        retry = now + timedelta(seconds=min(3600, 60 * (2 ** min(notice.email_attempts - 1, 6))))
        if notice.email_attempts >= MAX_ATTEMPTS:
            retry = None
        Notification.objects.filter(pk=notification_id, email_delivery_status='processing').update(
            email_delivery_status='failed', email_sent=False, email_next_retry_at=retry,
            email_last_error=message[:240])
        logger.warning('Mail notification %s failed (attempt %s, %s)', notification_id, notice.email_attempts, type(error).__name__)
        return 'failed'
