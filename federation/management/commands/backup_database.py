import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = 'Создать согласованную резервную копию SQLite или PostgreSQL. Старые копии не удаляются.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Путь нового файла. Существующий файл не перезаписывается.')
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        alias = options['database']
        if alias not in settings.DATABASES:
            raise CommandError('Неизвестное подключение БД.')
        config = settings.DATABASES[alias]
        engine = config['ENGINE']
        suffix = '.sqlite3' if engine.endswith('sqlite3') else '.dump'
        path = Path(options['output']) if options['output'] else Path(settings.LOCAL_DIR) / 'backups' / f'orenkod-{datetime.now():%Y%m%d-%H%M%S-%f}{suffix}'
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise CommandError('Файл уже существует. Выберите новый путь.')
        os.close(fd)
        try:
            if engine.endswith('sqlite3'):
                name = str(config['NAME'])
                if name == ':memory:' or name.startswith('file:memory'):
                    connections[alias].ensure_connection()
                    with sqlite3.connect(path) as target:
                        connections[alias].connection.backup(target)
                else:
                    source_path = Path(name).resolve()
                    with sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True) as source:
                        with sqlite3.connect(path) as target:
                            source.backup(target)
            elif engine.endswith('postgresql') or engine.endswith('postgresql_psycopg2'):
                if not shutil.which('pg_dump'):
                    raise CommandError('Нужен pg_dump версии не старше сервера PostgreSQL.')
                env = os.environ.copy()
                for key, field in [('PGDATABASE', 'NAME'), ('PGUSER', 'USER'), ('PGPASSWORD', 'PASSWORD'), ('PGHOST', 'HOST'), ('PGPORT', 'PORT')]:
                    if config.get(field):
                        env[key] = str(config[field])
                ssl_options = config.get('OPTIONS', {})
                for key, field in [('PGSSLMODE', 'sslmode'), ('PGSSLROOTCERT', 'sslrootcert'), ('PGSSLCERT', 'sslcert'), ('PGSSLKEY', 'sslkey')]:
                    if ssl_options.get(field):
                        env[key] = str(ssl_options[field])
                result = subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-privileges', '--file', str(path)], env=env, capture_output=True)
                if result.returncode:
                    raise CommandError('pg_dump не создал копию. Проверьте подключение и права роли БД; вывод с реквизитами не печатается.')
            else:
                raise CommandError('Поддерживаются SQLite и PostgreSQL.')
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.stdout.write(f'Резервная копия создана: {path}')
