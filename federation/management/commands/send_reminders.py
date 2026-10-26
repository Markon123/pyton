from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from federation.models import Application, Competition, Notification, Profile, RankApplication, Role
from federation.services import notify


class Command(BaseCommand):
    help = 'Отправить уведомления о приближении дедлайнов и окончании срока разрядов/категорий (повторный запуск безопасен).'

    def add_notification(self, user, title, body, url):
        # A stable title includes the deadline, so repeated jobs do not send duplicates.
        if not Notification.objects.filter(user=user, title=title).exists():
            notify(user, title, body, url)
            self.count += 1

    def handle(self, *args, **options):
        self.count = 0
        today = timezone.localdate()
        soon = today + timedelta(days=30)
        for rank in RankApplication.objects.filter(status='approved', valid_until__range=(today, soon)).select_related('athlete__user'):
            title = f'{rank.requested_title}: срок до {rank.valid_until:%d.%m.%Y}'
            body = f'Срок действия заканчивается через {(rank.valid_until - today).days} дней. Подготовьте заявление на подтверждение.'
            self.add_notification(rank.athlete.user, title, body, reverse('qualifications'))
            for link in rank.athlete.coach_links.filter(status='active').select_related('coach__user'):
                self.add_notification(link.coach.user, f'{rank.athlete.display_name} · {title}', body, reverse('qualifications'))
            for profile in Profile.objects.select_related('user'):
                if Role.FEDERATION in profile.roles:
                    self.add_notification(profile.user, f'{rank.athlete.display_name} · {title}', body, reverse('qualifications'))
        for event in Competition.objects.filter(state='open', registration_closes__range=(today, today + timedelta(days=3))):
            for app in event.applications.select_related('athlete__user', 'submitted_by'):
                title = f'Регистрация до {event.registration_closes:%d.%m.%Y} · {event.title}'
                body = 'Проверьте статус заявки и необходимые документы до закрытия регистрации.'
                self.add_notification(app.athlete.user, title, body, f'/competitions/{event.pk}/')
                if app.submitted_by_id != app.athlete.user_id:
                    self.add_notification(app.submitted_by, title, body, f'/competitions/{event.pk}/')
        self.stdout.write(self.style.SUCCESS(f'Создано уведомлений: {self.count}.'))
