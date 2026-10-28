"""Create private local or production settings without printing secrets."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def create_config(hostname, output, local=False, port=8000):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Локальный порт должен быть целым числом от 1024 до 65535.')
    if local:
        if hostname not in (None, 'localhost'):
            raise ValueError('Локальный режим использует localhost; домен задаётся отдельно через --domain.')
        host = 'localhost'
    else:
        if not isinstance(hostname, str):
            raise ValueError('Укажите DNS-имя через --domain или выберите --local.')
        if port != 8000:
            raise ValueError('--port применяется только в режиме --local.')
        host = hostname.lower().rstrip('.')
        if len(host) > 253 or '.' not in host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
            raise ValueError('Укажите DNS-имя без схемы, порта и пути, например platform.example.org.')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('Для автоматического HTTPS требуется DNS-имя, а не IP-адрес.')
    values = {
        'PUBLIC_HOST': host, 'PUBLIC_BASE_URL': 'https://' + host,
        'DJANGO_DEBUG': '0', 'FSP_SEED_DEMO': '0', 'TRUST_PROXY_HEADERS': '1',
        'DJANGO_SECRET_KEY': secrets.token_hex(48), 'POSTGRES_PASSWORD': secrets.token_hex(32),
        'DJANGO_ALLOWED_HOSTS': host + ',127.0.0.1,localhost,app',
        'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://' + host,
        'EMAIL_BACKEND': 'django.core.mail.backends.filebased.EmailBackend',
        'EMAIL_HOST': '', 'EMAIL_PORT': '587', 'EMAIL_USE_TLS': '1', 'EMAIL_USE_SSL': '0',
        'EMAIL_HOST_USER': '', 'EMAIL_HOST_PASSWORD': '', 'DEFAULT_FROM_EMAIL': 'noreply@' + host,
        'INTEGRATION_WEBHOOK_ALLOWED_HOSTS': '',
        'FSP_MINISTRY_USERNAME': 'ministry', 'FSP_MINISTRY_EMAIL': '', 'FSP_MINISTRY_PASSWORD': '',
    }
    if local:
        values.update({
            'FSP_PORT': str(port), 'PUBLIC_BASE_URL': f'http://localhost:{port}',
            'DJANGO_DEBUG': '1', 'TRUST_PROXY_HEADERS': '0',
            'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1,app',
            'DJANGO_CSRF_TRUSTED_ORIGINS': f'http://localhost:{port},http://127.0.0.1:{port}',
        })
    path = Path(output)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write('# Private settings. Configure real SMTP securely before enabling external delivery.\n')
        stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser(description='Подготовить закрытую конфигурацию локального или HTTPS-развёртывания.')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--domain')
    modes.add_argument('--local', action='store_true', help='Локальный HTTP на этом компьютере, без публичного домена.')
    parser.add_argument('--port', type=int, default=8000, help='Локальный порт для --local, по умолчанию 8000.')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        path = create_config(args.domain, args.output or ('.env.local' if args.local else '.env.production'), local=args.local, port=args.port)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Настройка не создана: {error}\n')
    print(f'Конфигурация создана: {path}. Секреты не выводятся; храните файл вне Git.')
    print('Локальный запуск не публикует сайт в интернете.' if args.local else 'Заполнение SMTP и доступ к серверу выполняются отдельно. Сайт пока не опубликован.')


if __name__ == '__main__':
    main()
