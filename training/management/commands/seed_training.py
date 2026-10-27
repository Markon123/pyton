from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from federation.access import role_profiles
from federation.models import CoachLink
from training.models import (AssessmentNorm, AssessmentResult, Attendance, Enrollment,
                             OrganizationProgram, Session, Stage, TrainingGroup)


class Command(BaseCommand):
    help = 'Добавить вымышленные программы, группы, занятия и демонстрационные нормативы без сброса данных.'

    @transaction.atomic
    def handle(self, *args, **options):
        coach = role_profiles('coach').filter(user__username='coach').first()
        federation = role_profiles('federation').first()
        if not coach or not federation or not coach.organization:
            self.stdout.write('Сначала выполните seed_demo: нужен демо-тренер с организацией.')
            return
        today = timezone.localdate()
        lesson_day = today - timedelta(days=1)
        created = 0
        for discipline in coach.disciplines.all():
            for stage in Stage.values:
                OrganizationProgram.objects.get_or_create(organization=coach.organization, discipline=discipline, stage=stage,
                    defaults={'license_details': 'ДЕМО-ЛИЦЕНЗИЯ 0000 — вымышленные реквизиты',
                              'description': 'Демонстрационная программа. Для внедрения замените реквизиты проверенными данными.'})
            initial, _ = TrainingGroup.objects.get_or_create(name=f'Демо · {discipline.code} · начальная подготовка',
                defaults={'organization': coach.organization, 'discipline': discipline, 'stage': Stage.INITIAL, 'coach': coach})
            TrainingGroup.objects.get_or_create(name=f'Демо · {discipline.code} · учебно-тренировочный этап',
                defaults={'organization': coach.organization, 'discipline': discipline, 'stage': Stage.TRAINING, 'coach': coach})
            speed, _ = AssessmentNorm.objects.get_or_create(name='[Демо] Скорость печати', discipline=discipline, stage=Stage.INITIAL,
                defaults={'sex': 'all', 'unit': 'знаков/мин', 'threshold': Decimal('200'), 'direction': 'gte', 'demonstration': True,
                          'source': 'Условный пример для демонстрации механики, не норматив ФССП'})
            sprint, _ = AssessmentNorm.objects.get_or_create(name='[Демо] Время выполнения специального задания', discipline=discipline, stage=Stage.INITIAL,
                defaults={'sex': 'all', 'unit': 'сек', 'threshold': Decimal('90'), 'direction': 'lte', 'demonstration': True,
                          'source': 'Условный пример для демонстрации механики, не норматив ФССП'})
            lesson, _ = Session.objects.get_or_create(group=initial, topic='Демо-занятие: разбор задач и командная практика',
                defaults={'date': lesson_day, 'format': 'offline', 'created_by': coach.user})
            for link in CoachLink.objects.filter(coach=coach, discipline=discipline, status='active').select_related('athlete')[:4]:
                person = link.athlete
                # Never invent a new competing interval when a user already enrolled this athlete.
                enrollment = Enrollment.objects.filter(athlete=person, group__discipline=discipline).first()
                if enrollment is None:
                    enrollment = Enrollment(athlete=person, group=initial, start_date=date(max(today.year - 1, person.start_year), 1, 1),
                                            municipality=person.municipality or coach.organization.municipality)
                    enrollment.full_clean()
                    enrollment.save()
                    created += 1
                if enrollment.group_id != initial.pk or enrollment.start_date > lesson.date or (enrollment.end_date and enrollment.end_date < lesson.date):
                    continue
                Attendance.objects.get_or_create(session=lesson, athlete=person, defaults={'present': True})
                for norm, value in ((speed, Decimal('240')), (sprint, Decimal('75'))):
                    if not AssessmentResult.objects.filter(athlete=person, norm=norm).exists():
                        result = AssessmentResult(athlete=person, norm=norm, value=value, tested_on=lesson.date, recorded_by=coach.user)
                        result.full_clean()
                        result.save()
        self.stdout.write(self.style.SUCCESS(f'Подготовка: {TrainingGroup.objects.count()} групп, {Enrollment.objects.count()} зачислений, {AssessmentNorm.objects.count()} нормативов. Новых зачислений: {created}. Все пороги в демо — условные.'))
