from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from oauth2_provider.models import get_application_model

from identity.config import signing_key
from identity.validator import DEMO_CLIENT_ID


class Command(BaseCommand):
    help = 'Проверяет постоянный ключ OIDC; --demo регистрирует локальный тестовый PKCE-клиент.'

    def add_arguments(self, parser):
        parser.add_argument('--demo', action='store_true')
        parser.add_argument('--base-url', default='http://127.0.0.1:8000')

    def handle(self, *args, **options):
        signing_key(settings.LOCAL_DIR)
        self.stdout.write('Ключ подписи ФСП ID проверен; закрытый ключ не выводится.')
        if not options['demo']:
            return
        parsed = urlsplit(options['base_url'])
        if not settings.DEBUG or parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise CommandError('Демо-клиент допускается только в DEBUG на точном loopback-адресе.')
        app_model = get_application_model()
        app, created = app_model.objects.get_or_create(client_id=DEMO_CLIENT_ID, defaults={
            'name': 'Демонстрационный клиент ФСП ID', 'client_type': app_model.CLIENT_PUBLIC,
            'client_secret': '', 'authorization_grant_type': app_model.GRANT_AUTHORIZATION_CODE,
            'algorithm': app_model.RS256_ALGORITHM, 'skip_authorization': False,
            'redirect_uris': options['base_url'].rstrip('/') + '/identity/demo/callback/',
        })
        self.stdout.write('Локальный клиент зарегистрирован.' if created else 'Локальный клиент уже существует; настройки сохранены.')
