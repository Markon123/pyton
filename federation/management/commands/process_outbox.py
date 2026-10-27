from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from federation.mail_delivery import deliver_notification, due_notifications
from federation.models import Notification


class Command(BaseCommand):
    help = 'Доставить ожидающие письма и повторить временно неуспешные отправки.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--retry-local', action='store_true', help='Явно повторить локальные письма через текущий транспорт.')
        parser.add_argument('--retry-failed', action='store_true', help='Вручную повторить failed, включая исчерпавшие лимит попыток.')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 10000:
            raise CommandError('--limit должен быть от 1 до 10000.')
        queued = due_notifications(include_local=options['retry_local'])
        if options['retry_failed']:
            queued = Notification.objects.filter(Q(pk__in=queued.values('pk')) | Q(email_delivery_status='failed')).order_by('created_at', 'pk')
        ids = list(queued.values_list('pk', flat=True)[:limit])
        outcomes = Counter(deliver_notification(pk, force=options['retry_local'] or options['retry_failed']) for pk in ids)
        self.stdout.write('Почтовая очередь: ' + (', '.join(f'{key}={value}' for key, value in sorted(outcomes.items()) if key) or 'нет ожидающих писем'))
