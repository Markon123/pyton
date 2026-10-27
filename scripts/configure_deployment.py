"""Create a private production env file; never deploy or print its secrets."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets


def create_config(hostname, output):
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
        'EMAIL_HOST': '', 'EMAIL_PORT': '587', 'EMAIL_USE_TLS': '1',
        'EMAIL_HOST_USER': '', 'EMAIL_HOST_PASSWORD': '', 'DEFAULT_FROM_EMAIL': 'noreply@' + host,
        'INTEGRATION_WEBHOOK_ALLOWED_HOSTS': '',
    }
    path = Path(output)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write('# Private production settings. Configure real SMTP securely before enabling external delivery.\n')
        stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser(description='Подготовить закрытую конфигурацию HTTPS-развёртывания.')
    parser.add_argument('--domain', required=True)
    parser.add_argument('--output', default='.env.production')
    args = parser.parse_args()
    try:
        path = create_config(args.domain, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Настройка не создана: {error}\n')
    print(f'Конфигурация создана: {path}. Секреты не выводятся; храните файл вне Git.')
    print('Заполнение SMTP и доступ к серверу выполняются отдельно. Сайт пока не опубликован.')


if __name__ == '__main__':
    main()
