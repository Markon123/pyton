from django.core.management.base import BaseCommand, CommandError
from reporting.services import process_reports


class Command(BaseCommand):
    help = 'Обработать очередь фоновых отчётов, включая задачи с истёкшей арендой.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=5)

    def handle(self, **options):
        if not 1 <= options['limit'] <= 100:
            raise CommandError('--limit должен быть от 1 до 100.')
        count = process_reports(options['limit'])
        self.stdout.write(f'Готово фоновых отчётов: {count}.')
