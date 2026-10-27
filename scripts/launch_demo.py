#!/usr/bin/env python3
"""Portable local demo launcher. No credentials or external hosting required."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import venv
import webbrowser


ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / '.local' / 'launcher.log'


def main():
    parser = argparse.ArgumentParser(description='Установка и запуск демонстрационного ОренКода на этом компьютере.')
    parser.add_argument('--no-browser', action='store_true', help='Не открывать браузер автоматически.')
    parser.add_argument('--check-only', action='store_true', help='Подготовить и проверить приложение без запуска сервера.')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        print('Нужен Python 3.12 или новее. Установите его с python.org.')
        return 1
    os.chdir(ROOT)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('a', encoding='utf-8') as log:
        def say(message):
            print(message, flush=True)
            log.write(message + '\n')
            log.flush()

        def stream(proc):
            for line in proc.stdout:
                say(line.rstrip('\r\n'))

        def run(*command):
            with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding='utf-8', errors='replace') as proc:
                try:
                    stream(proc)
                    code = proc.wait()
                except KeyboardInterrupt:
                    stop(proc)
                    raise
            if code:
                raise RuntimeError(f'Команда {Path(command[0]).name} завершилась с кодом {code}. См. сообщение выше.')

        server = None
        scheduler = None
        output = None
        scheduler_output = None
        try:
            say(f'\nОренКод · запуск {datetime.now():%Y-%m-%d %H:%M:%S}')
            try:
                port = int(os.environ.get('FSP_PORT', '8000'))
            except ValueError:
                raise RuntimeError('FSP_PORT должен быть целым номером порта.')
            if not 1024 <= port <= 65535:
                raise RuntimeError('Выберите FSP_PORT в диапазоне 1024–65535.')
            if not args.check_only:
                with socket.socket() as probe:
                    try:
                        probe.bind(('127.0.0.1', port))
                    except OSError:
                        raise RuntimeError(f'Порт {port} уже занят. Возможно, приложение уже работает в другом окне. Закройте его или выберите другой FSP_PORT.')
            env_dir = Path(os.environ.get('FSP_VENV_DIR', str(ROOT / '.venv'))).resolve()
            python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if not python.is_file():
                say('Создаю виртуальное окружение Python…')
                try:
                    venv.EnvBuilder(with_pip=True).create(env_dir)
                except Exception as error:
                    raise RuntimeError('Не удалось создать окружение. На Linux проверьте установку python3-venv. ' + str(error))
            say('Устанавливаю зависимости. При первом запуске нужен интернет…')
            run(str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(ROOT / 'requirements.txt'))
            say('Подготавливаю базу и вымышленные демонстрационные данные…')
            for command in ('migrate', 'prepare_project', 'seed_full_demo', 'collectstatic', 'check', 'send_reminders'):
                extra = ['--noinput'] if command in ('migrate', 'collectstatic') else []
                run(str(python), 'manage.py', command, *extra)
            if args.check_only:
                say('Настройка и проверка завершены успешно. Для открытия сайта запустите файл ещё раз без --check-only.')
                return 0
            say('Запускаю приложение…')
            server = subprocess.Popen([str(python), 'manage.py', 'runserver', f'127.0.0.1:{port}', '--noreload'],
                                      cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding='utf-8', errors='replace')
            output = threading.Thread(target=stream, args=(server,), daemon=True)
            output.start()
            scheduler = subprocess.Popen([str(python), 'manage.py', 'run_scheduler', '--interval=60'],
                                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding='utf-8', errors='replace')
            scheduler_output = threading.Thread(target=stream, args=(scheduler,), daemon=True)
            scheduler_output.start()
            # These URLs are used locally by this launcher, never as cloud previews.
            base_url = f'http://127.0.0.1:{port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    output.join(timeout=2)
                    raise RuntimeError('Сервер завершился до запуска. Проверьте сообщение выше.')
                try:
                    with opener.open(base_url + '/health/', timeout=1) as response:
                        ready = response.status == 200 and json.load(response) == {'status': 'ok'}
                    if ready:
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                time.sleep(.2)
            if not ready:
                raise RuntimeError('Сервер не прошёл проверку готовности за 30 секунд. Подробности в .local/launcher.log.')
            say(f'Готово. Приложение работает локально на порту {port}.')
            say('Вход: federation / FspDemo2026!')
            say('Не закрывайте это окно во время работы. Для остановки нажмите Ctrl+C.')
            if not args.no_browser:
                if not webbrowser.open(base_url + '/login/'):
                    say('Браузер не открылся автоматически. Откройте локальное приложение на указанном порту.')
            code = server.wait()
            output.join(timeout=2)
            if code:
                raise RuntimeError(f'Сервер завершился с кодом {code}.')
            return 0
        except KeyboardInterrupt:
            say('Приложение остановлено.')
            return 0
        except Exception as error:
            say(f'Ошибка: {error}')
            say(f'Журнал: {LOG}')
            return 1
        finally:
            if server is not None:
                stop(server)
            if scheduler is not None:
                stop(scheduler)
            if output is not None:
                output.join(timeout=2)
            if scheduler_output is not None:
                scheduler_output.join(timeout=2)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
