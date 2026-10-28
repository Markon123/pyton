from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from federation.models import CoachLink, Competition, Profile, RankApplication, Role
from federation.deadlines import dispatch_reminder_once, send_report_deadline_reminders


class Command(BaseCommand):
    help = 'Отправить уведомления о приближении дедлайнов и окончании срока разрядов/категорий (повторный запуск безопасен).'

    def add_notification(self, user, title, body, url):
        if dispatch_reminder_once(user, title, body, url):
            self.count += 1

    def handle(self, *args, **options):
        self.count = 0
        today = timezone.localdate()
        soon = today + timedelta(days=30)
        profiles = list(Profile.objects.filter(status='active', user__is_active=True).select_related('user').prefetch_related('disciplines'))
        federation = [person for person in profiles if Role.FEDERATION in person.roles]
        coach_links = CoachLink.objects.filter(status='active', coach__status='active', coach__user__is_active=True).select_related('coach__user')
        ranks = RankApplication.objects.filter(status='approved', valid_until__range=(today, soon),
            athlete__status='active', athlete__user__is_active=True).select_related('athlete__user', 'result__application').prefetch_related(
                Prefetch('athlete__coach_links', queryset=coach_links, to_attr='reminder_coach_links'))
        for rank in ranks:
            title = f'{rank.requested_title}: срок до {rank.valid_until:%d.%m.%Y}'
            body = f'Срок действия заканчивается через {(rank.valid_until - today).days} дней. Подготовьте заявление на подтверждение.'
            self.add_notification(rank.athlete.user, title, body, reverse('qualifications'))
            receivers = {profile.user_id: profile.user for profile in federation}
            for link in rank.athlete.reminder_coach_links:
                if Role.COACH in link.coach.roles and (not rank.result_id or link.discipline_id == rank.result.application.discipline_id):
                    receivers[link.coach.user_id] = link.coach.user
            for user in receivers.values():
                self.add_notification(user, f'{rank.athlete.display_name} · {title}', body, reverse('qualifications'))

        # Compute eligibility from prefetched profiles once; an athlete does not
        # need an existing application in order to receive the closing reminder.
        from governance.services import active_disqualifications
        disqualified = set(active_disqualifications(today).values_list('athlete_id', flat=True))
        athletes = [person for person in profiles if Role.ATHLETE in person.roles and person.pk not in disqualified
                    and (not person.disqualified_until or person.disqualified_until < today)]
        disciplines = {person.pk: {discipline.pk for discipline in person.disciplines.all()} for person in athletes}
        linked_coaches = {}
        for link in coach_links.filter(athlete_id__in=[person.pk for person in athletes]):
            if Role.COACH in link.coach.roles:
                linked_coaches.setdefault(link.athlete_id, []).append(link)
        events = Competition.objects.filter(state='open', registration_opens__lte=today,
            registration_closes__range=(today, today + timedelta(days=3))).prefetch_related('disciplines')
        for event in events:
            event_disciplines = {discipline.pk for discipline in event.disciplines.all()}
            receivers = {person.user_id: person.user for person in federation}
            for person in athletes:
                age = person.age_on(event.starts_on)
                matching = disciplines[person.pk] & event_disciplines
                if age is None or not event.age_min <= age <= event.age_max or not matching:
                    continue
                receivers[person.user_id] = person.user
                for link in linked_coaches.get(person.pk, []):
                    if link.discipline_id in matching:
                        receivers[link.coach.user_id] = link.coach.user
            title = f'Регистрация № {event.pk} до {event.registration_closes:%d.%m.%Y} · {event.title}'
            body = 'Приём заявок скоро закроется. Если вы планируете участие, отправьте заявку и проверьте необходимые документы до указанного срока.'
            for user in receivers.values():
                self.add_notification(user, title, body, reverse('competition_detail', args=[event.pk]))
        send_report_deadline_reminders(self.add_notification, today)
        self.stdout.write(self.style.SUCCESS(f'Создано уведомлений: {self.count}.'))
