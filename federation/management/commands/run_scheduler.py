import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Запускать почтовую очередь/вебхуки и ежедневные напоминания. Запускайте один экземпляр.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        if not 1 <= options['interval'] <= 3600:
            raise CommandError('--interval должен быть от 1 до 3600 секунд.')
        state_path = Path(settings.LOCAL_DIR) / 'scheduler-state.json'
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict):
                state = {}
        except (FileNotFoundError, ValueError):
            state = {}
        try:
            while True:
                today = timezone.localdate().isoformat()
                commands = get_commands()
                for command in ('send_reminders', 'award_reminders'):
                    if command not in commands or state.get(command) == today:
                        continue
                    if self.run_task(command):
                        state[command] = today
                        temporary = state_path.with_suffix('.tmp')
                        temporary.write_text(json.dumps(state), encoding='utf-8')
                        temporary.replace(state_path)
                for command in ('process_outbox', 'deliver_webhooks', 'process_reports'):
                    if command in commands:
                        self.run_task(command)
                if options['once']:
                    return
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Планировщик остановлен.')

    def run_task(self, name):
        try:
            call_command(name, stdout=self.stdout, stderr=self.stderr)
            return True
        except Exception as error:
            # Credentials and SMTP errors must not be copied into scheduler logs.
            self.stderr.write(f'{name}: сбой ({type(error).__name__}), повтор в следующем цикле.')
            return False
