from django.core.management.base import BaseCommand
from django.utils.text import slugify
from federation.forms import RANK_TITLES, CATEGORY_TITLES
from federation.models import DictionaryEntry, DocumentLayout


class Command(BaseCommand):
    help = 'Добавить базовые справочники и макеты без изменения существующих записей.'

    def handle(self, **options):
        values = {'competition_type': ['Чемпионат', 'Первенство', 'Кубок', 'Турнир', 'Хакатон'],
                  'rank': RANK_TITLES, 'category': CATEGORY_TITLES,
                  'stage': ['Начальная подготовка', 'Учебно-тренировочный этап', 'Совершенствование спортивного мастерства', 'Высшее спортивное мастерство'],
                  'municipality': ['Оренбург', 'Орск', 'Бузулук', 'Новотроицк'],
                  'age_group': ['До 14 лет', '14–17 лет', '18–25 лет', '26 лет и старше']}
        for kind, labels in values.items():
            for i, label in enumerate(labels):
                DictionaryEntry.objects.get_or_create(kind=kind, code=f'{kind}-{i+1}', defaults={'label': label})
        for kind, _ in DocumentLayout._meta.get_field('kind').choices:
            DocumentLayout.objects.get_or_create(kind=kind, defaults={
                'heading': 'Региональное отделение ФСП Оренбургской области',
                'footer': 'Подготовлено в платформе ОренКод. Проверил: __________________. Подпись: __________________.',
            })
        self.stdout.write('Справочники и макеты подготовлены. Существующие настройки сохранены.')
