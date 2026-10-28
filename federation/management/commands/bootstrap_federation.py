from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Создание федерации перенесено в главный кабинет министерства.'

    def add_arguments(self, parser):
        parser.add_argument('--username')
        parser.add_argument('--email')

    def handle(self, **options):
        raise CommandError('Федерации создаёт только министерство через раздел «Аккаунты федераций». Для первоначального запуска выполните bootstrap_ministry.')
