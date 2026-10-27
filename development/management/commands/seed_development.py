from django.core.management.base import BaseCommand
from django.utils import timezone
from federation.models import Discipline, Profile
from development.models import Evidence, RankCriterion


class Command(BaseCommand):
    help = 'Добавить явно демонстрационные критерии и источники показателей без сброса существующих записей.'

    def handle(self, *args, **options):
        for discipline in Discipline.objects.all():
            RankCriterion.objects.get_or_create(discipline=discipline, rank_title='III разряд', is_demo=True,
                source_text='Учебный пример: призовое место в региональном старте. Это НЕ норматив ЕВСК. Федерация должна заменить критерий действующим нормативом перед применением.',
                defaults={'levels': ['regional', 'interregional', 'russian_championship', 'russian_junior'], 'max_place': 3,
                          'min_score': 0, 'age_min': 12, 'age_max': 99})
        person = next((p for p in Profile.objects.select_related('user') if 'federation' in p.roles), None)
        if person:
            Evidence.objects.get_or_create(year=timezone.localdate().year, indicator='participation',
                citation='Источник демонстрационного показателя: интервалы начала и окончания занятий в реестре и зачисления в учебно-тренировочные группы. Все исходные записи вымышлены.',
                defaults={'created_by': person.user})
        self.stdout.write(self.style.SUCCESS('Критерии и источники развития добавлены. Демонстрационные пороги не являются нормами ЕВСК.'))
