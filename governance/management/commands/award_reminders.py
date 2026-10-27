from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone
from federation.access import role_profiles
from federation.models import Notification
from federation.services import notify


class Command(BaseCommand):
    help = 'Напоминание федерации о сроке ходатайств на премии до 1 февраля.'

    def handle(self, *args, **options):
        today = timezone.localdate()
        deadline = date(today.year, 2, 1)
        if not deadline - timedelta(days=31) <= today < deadline:
            self.stdout.write('Вне периода напоминания о премиях. Создано: 0.')
            return
        count = 0
        title = f'Премии: ходатайства за {today.year - 1} год до 1 февраля {today.year}'
        for person in role_profiles('federation'):
            if not Notification.objects.filter(user=person.user, title=title).exists():
                notify(person.user, title, 'Проверьте кандидатов и подготовьте ходатайства до истечения срока.', reverse('governance:awards') + f'?year={today.year - 1}')
                count += 1
        self.stdout.write(f'Напоминаний о премиях создано: {count}.')
