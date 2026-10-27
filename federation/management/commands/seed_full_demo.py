from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Повторяемое наполнение всех модулей вымышленными демонстрационными данными.'

    def handle(self, **options):
        for command in ['prepare_project', 'seed_demo', 'seed_training', 'seed_governance', 'seed_development', 'setup_identity']:
            kwargs = {'verbosity': options.get('verbosity', 1)}
            if command == 'setup_identity':
                kwargs['demo'] = settings.DEBUG
            call_command(command, **kwargs)
        self.stdout.write(self.style.SUCCESS('Все модули демонстрационного проекта подготовлены.'))
