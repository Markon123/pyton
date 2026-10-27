from django.core.management.base import BaseCommand
from integrations.services import deliver_pending


class Command(BaseCommand):
    help = 'Доставить до 50 ожидающих webhook с повторными попытками и HMAC-подписью.'

    def handle(self, *args, **options):
        self.stdout.write(f'Доставлено webhook: {deliver_pending()}')
