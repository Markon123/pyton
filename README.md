# ОренКод · развёртывание и эксплуатация

Единая платформа ФСП Оренбургской области: кабинеты министерства, федерации, спортсмена, тренера и судьи; соревнования, заявки, результаты, квалификации, рейтинг, сборная, отчёты и дополнительные модули.

**[Скачать ZIP проекта](https://github.com/Markon123/pyton/archive/refs/heads/main.zip)**

Инструкция рассчитана на запуск на собственном ПК и на дальнейшее размещение серверной версии. Для обычного запуска не нужны Node.js, npm, отдельный frontend-сервер или облачный аккаунт.

## 1. Какой способ выбрать

| Способ | Что установить | База | Когда использовать |
| --- | --- | --- | --- |
| Docker Compose — рекомендуется DevOps | Docker Engine/Desktop и Compose v2; Python нужен только для генератора настроек, ниже есть Docker-альтернатива | PostgreSQL 16 в контейнере | Воспроизводимый локальный стенд и подготовка сервера |
| start.bat / start.sh | Python 3.12+ | SQLite | Самый простой запуск на ПК |
| Python вручную | Python 3.12+, виртуальное окружение | SQLite или свой PostgreSQL | Разработка и диагностика |
| Сервер с HTTPS | Docker Compose, сервер, DNS-имя, доступные 80/443 | PostgreSQL | Доступ пользователей через интернет |

Проверенная версия Python — 3.12. Зависимости закреплены в requirements.txt. Для Docker на Windows выберите Linux containers и запустите Docker Desktop. Примерный стартовый ориентир для локального Docker — 2 CPU, 4 ГБ RAM и несколько ГБ свободного диска; это не результат нагрузочного тестирования.

## 2. Получение исходников

Через Git:

~~~bash
git clone https://github.com/Markon123/pyton.git
cd pyton
~~~

Либо скачайте ZIP, полностью извлеките его и откройте терминал в папке pyton-main. В Windows нельзя запускать проект прямо внутри архива.

Команды далее выполняются из корня проекта, где находятся compose.yaml, manage.py и requirements.txt.

## 3. Быстрый запуск Docker на своём ПК

### 3.1. Проверить инструменты

В PowerShell, Terminal или Bash:

~~~text
docker version
docker compose version
~~~

Нужен именно Compose v2: команда docker compose, без дефиса. Если docker version сообщает, что сервер недоступен, сначала запустите Docker Desktop/службу Docker.

### 3.2. Создать закрытый файл локальных настроек

Windows PowerShell:

~~~powershell
py -3 scripts/configure_deployment.py --local --output .env.local
~~~

Linux/macOS:

~~~bash
python3 scripts/configure_deployment.py --local --output .env.local
~~~

Генератор создаёт отдельные случайные DJANGO_SECRET_KEY и POSTGRES_PASSWORD. Локальный режим использует HTTP, DEBUG=1 и адрес localhost; рабочие данные при этом не заполняются учебными примерами. Файл исключён из Git, существующий файл генератор не перезаписывает.

Если Python на хосте отсутствует, генератор можно выполнить официальным Python-образом.

Windows PowerShell:

~~~powershell
docker run --rm --mount "type=bind,source=$($PWD.Path),target=/project" -w /project python:3.12-slim-bookworm python scripts/configure_deployment.py --local --output .env.local
~~~

Linux/macOS — файл будет принадлежать текущему пользователю:

~~~bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/project" -w /project python:3.12-slim-bookworm python scripts/configure_deployment.py --local --output .env.local
~~~

Если .env.local уже существует, используйте его повторно. Не генерируйте новый пароль БД поверх настроек существующего тома PostgreSQL.

### 3.3. Проверить конфигурацию и запустить

Одинаковые команды для всех ОС:

~~~text
docker compose --env-file .env.local -p orenkod-local config --quiet
docker compose --env-file .env.local -p orenkod-local up --build -d
docker compose --env-file .env.local -p orenkod-local ps
~~~

Первая сборка загружает образы и Python-пакеты. При запуске entrypoint автоматически выполняет миграции, collectstatic, prepare_project, bootstrap_ministry и системную проверку. Отдельно запускать seed_* не нужно.

Ожидаются сервисы:

- db — PostgreSQL 16, состояние healthy;
- app — Gunicorn, состояние healthy;
- scheduler — работающий планировщик;
- mailpit отсутствует, если его профиль специально не включён.

Приложение на вашем ПК:

~~~text
http://127.0.0.1:8000
~~~

Это адрес собственного компьютера. Он не является публичным стендом и не открывает сайт другим людям.

Проверка здоровья, PowerShell:

~~~powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/
~~~

Linux/macOS:

~~~bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/
~~~

Ожидаемый JSON: {"status":"ok"}.

### 3.4. Первый вход министерства

До открытия интерфейса в БД создаётся **один главный аккаунт министерства**. Общего опубликованного пароля нет. Временный пароль генерируется для каждой установки отдельно и не печатается в журналах.

Скопируйте файл доступа из контейнера в игнорируемую локальную папку.

PowerShell:

~~~powershell
New-Item -ItemType Directory -Force .local | Out-Null
docker compose --env-file .env.local -p orenkod-local cp app:/app/.local/first-access.txt .local/docker-first-access.txt
notepad .local/docker-first-access.txt
~~~

Linux/macOS:

~~~bash
mkdir -p .local
docker compose --env-file .env.local -p orenkod-local cp app:/app/.local/first-access.txt .local/docker-first-access.txt
chmod 600 .local/docker-first-access.txt
~~~

Откройте файл своим текстовым редактором. Логин по умолчанию — ministry. При первом входе задайте собственный пароль и подтвердите согласие. После смены временный пароль из файла больше не действует.

Если оператор заранее задал FSP_MINISTRY_PASSWORD в защищённых настройках, файл доступа не создаётся: используйте пароль из своего защищённого хранилища. Повторный запуск не меняет пароль существующего министерства.

Не публикуйте first-access.txt, содержимое .local и .env.local; не пересылайте их в чат.

### 3.5. Создать федерацию и пользователей

1. Войдите министерством и завершите первый вход.
2. Откройте **«Аккаунты федераций»**.
3. Укажите название, логин, рабочий email и временный пароль сотрудника.
4. Передайте ему данные защищённым способом.
5. Сотрудник федерации при первом входе сменит пароль и подтвердит согласие.
6. Спортсмены, тренеры и судьи регистрируются самостоятельно на странице регистрации.

Федерация и министерство недоступны в публичной регистрации. Только единственный главный аккаунт министерства создаёт федерации. Технические права Django superuser при этом не выдаются.

## 4. Повседневные команды Docker

**Во всех командах сохраняйте те же --env-file и -p.** Иначе Compose может выбрать другие параметры, пароли или тома.

~~~text
docker compose --env-file .env.local -p orenkod-local ps
docker compose --env-file .env.local -p orenkod-local logs --tail=100 app scheduler db
docker compose --env-file .env.local -p orenkod-local logs -f app scheduler
docker compose --env-file .env.local -p orenkod-local restart app scheduler
docker compose --env-file .env.local -p orenkod-local stop
docker compose --env-file .env.local -p orenkod-local start
docker compose --env-file .env.local -p orenkod-local down
~~~

Ctrl+C при просмотре logs -f прекращает только просмотр логов. Обычный down сохраняет именованные тома. **Не используйте down -v для обычной остановки: он удаляет данные.**

После изменения переменных в .env.local одного restart недостаточно — пересоздайте процессы:

~~~text
docker compose --env-file .env.local -p orenkod-local up -d --force-recreate app scheduler
~~~

После изменения исходников пересоберите образ:

~~~text
docker compose --env-file .env.local -p orenkod-local up --build -d
~~~

Диагностика и обслуживание:

~~~text
docker compose --env-file .env.local -p orenkod-local exec app python manage.py check
docker compose --env-file .env.local -p orenkod-local exec app python manage.py showmigrations
docker compose --env-file .env.local -p orenkod-local exec app python manage.py migrate --noinput
docker compose --env-file .env.local -p orenkod-local exec app python manage.py collectstatic --noinput
docker compose --env-file .env.local -p orenkod-local exec app python manage.py run_scheduler --once
~~~

Не запускайте второй постоянный scheduler рядом с уже работающим сервисом без необходимости. Команда --once предназначена для диагностики.

## 5. Запуск без Docker

### Windows: автоматический

Установите Python 3.12+ с параметром Add Python to PATH, распакуйте проект и запустите **start.bat**. Окно установки должно оставаться открытым. Браузер откроется после проверки приложения.

Первоначальные данные находятся в **.local/first-access.txt** на вашем ПК. Лаунчер создаёт .venv, рабочую SQLite-базу .local/platform.sqlite3 и запускает сервер вместе с планировщиком. Вымышленных участников и соревнований он не добавляет.

### Linux/macOS: автоматический

~~~bash
bash start.sh
~~~

Нужен Python 3.12+ с модулем venv. На Linux пакет для venv может устанавливаться отдельно средствами вашей ОС.

### Ручной запуск — Windows PowerShell

Активация PowerShell-скрипта окружения не обязательна: можно вызывать Python напрямую, без изменения ExecutionPolicy.

~~~powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate --noinput
.\.venv\Scripts\python.exe manage.py prepare_project
.\.venv\Scripts\python.exe manage.py bootstrap_ministry
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000 --noreload
~~~

Во **втором** PowerShell-окне, из той же папки:

~~~powershell
.\.venv\Scripts\python.exe manage.py run_scheduler --interval 60
~~~

### Ручной запуск — Linux/macOS

Перед созданием окружения убедитесь, что python3 имеет версию 3.12+.

~~~bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py prepare_project
python manage.py bootstrap_ministry
python manage.py collectstatic --noinput
python manage.py check
python manage.py runserver 127.0.0.1:8000 --noreload
~~~

В другом терминале:

~~~bash
. .venv/bin/activate
python manage.py run_scheduler --interval 60
~~~

При ручном запуске Ctrl+C останавливает соответствующий процесс. В автоматическом лаунчере Ctrl+C останавливает и сервер, и его планировщик. Сервер разработки предназначен для локального ПК; публичный сервер запускайте через Gunicorn/Compose и HTTPS.

## 6. Настройки и порядок их применения

Приложение читает переменные **окружения процесса**. Обычный Python-запуск сам по себе не загружает произвольный .env.

В Docker файл --env-file используется Compose для подстановки параметров; переменные текущей оболочки могут иметь приоритет. Значения передаются приложению через environment в compose.yaml. Не используйте разные файлы настроек для app и scheduler.

| Переменная | Назначение |
| --- | --- |
| DJANGO_DEBUG | 1 для локального HTTP, 0 для сервера |
| DJANGO_SECRET_KEY | Уникальный секрет установки |
| DJANGO_ALLOWED_HOSTS | Имена хостов без схемы/порта, через запятую |
| DJANGO_CSRF_TRUSTED_ORIGINS | Полные доверенные origin со схемой и портом |
| DATABASE_URL | PostgreSQL; если задан, имеет приоритет над DATABASE_PATH |
| DATABASE_PATH | SQLite-файл для запуска без Docker |
| PUBLIC_BASE_URL | Канонический адрес сайта для QR и OIDC |
| FSP_PORT | Порт локального лаунчера / опубликованный порт Compose |
| FSP_VENV_DIR | Путь готового окружения Python для лаунчера |
| FSP_MINISTRY_USERNAME | Первоначальный логин министерства, по умолчанию ministry |
| FSP_MINISTRY_EMAIL | Первоначальный email, необязательный |
| FSP_MINISTRY_PASSWORD | Необязательный первоначальный секрет из защищённого окружения |
| EMAIL_* | Почтовый транспорт |
| TRUST_PROXY_HEADERS | 1 только за доверенным proxy, удаляющим клиентские forwarded-заголовки |
| INTEGRATION_WEBHOOK_ALLOWED_HOSTS | Разрешённые HTTPS-получатели вебхуков |

Министерские переменные применяются при **создании** главного аккаунта. Их изменение не сбрасывает пароль уже созданного пользователя.

### Другой локальный порт

Для новой установки можно использовать поддерживаемый генератором параметр --port. Для существующей установки измените только нужные параметры .env.local, сохранив секреты:

~~~dotenv
FSP_PORT=8080
PUBLIC_BASE_URL=http://localhost:8080
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
~~~

Затем выполните up -d --force-recreate app scheduler с прежним --env-file и -p. Внутри Docker приложение продолжает слушать порт 8000; меняется порт вашего ПК.

Без Docker, PowerShell:

~~~powershell
$env:FSP_PORT = "8080"
.\start.bat
~~~

Без Docker, Bash:

~~~bash
FSP_PORT=8080 bash start.sh
~~~

### Свой PostgreSQL без Docker-приложения

Задайте DATABASE_URL в защищённом окружении перед migrate и запуском обоих процессов. Формат подключения:

~~~text
postgresql://USER:URL_ENCODED_PASSWORD@HOST:5432/DATABASE?sslmode=verify-full
~~~

Это схема для заполнения своими данными, а не готовые реквизиты. Спецсимволы логина/пароля кодируйте для URL. Для удалённой БД настройте проверку TLS и доверенный сертификат. PostgreSQL-клиенты pg_dump/pg_restore должны быть совместимы с сервером; в Docker предусмотрена версия 16.

Docker и локальный Python по умолчанию используют **разные базы**. SQLite-файл нельзя переименованием превратить в PostgreSQL.

## 7. Почта и фоновые задачи

Внутренние уведомления сохраняются в БД независимо от SMTP. По умолчанию письма записываются локально. Это не доказательство доставки во внешний почтовый ящик.

Для своего почтового сервиса заполните в закрытом .env.local/серверном файле:

~~~dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.example
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
DEFAULT_FROM_EMAIL=
~~~

Для SMTP через SSL обычно используются EMAIL_PORT=465, EMAIL_USE_SSL=1, EMAIL_USE_TLS=0. Оба режима одновременно включать нельзя. Сертификаты проверяются.

После изменения настроек Docker пересоздайте app и scheduler. Без Docker перезапустите оба процесса с обновлённым окружением.

В кабинете федерации откройте **«Уведомления» → «Доставка уведомлений»**. Кнопка создаёт тест только на адрес текущего сотрудника. Статус «Принято почтовым сервером» подтверждает SMTP ACK; получение проверяйте в самом ящике, включая «Спам».

Планировщик обрабатывает почтовую очередь, вебхуки, большие отчёты и напоминания. Диагностика очереди:

~~~text
docker compose --env-file .env.local -p orenkod-local exec app python manage.py process_outbox --limit 100
docker compose --env-file .env.local -p orenkod-local exec app python manage.py process_reports
~~~

Параметры --retry-local/--retry-failed применяйте намеренно после проверки адресатов: они могут вызвать реальную повторную отправку. [Подробности почты](docs/mail.md), [фоновых отчётов](docs/background-reports.md).

## 8. Где лежат данные

| Данные | Docker | Без Docker |
| --- | --- | --- |
| PostgreSQL | Именованный том postgres_data проекта Compose | Ваш сервер PostgreSQL |
| SQLite | При штатном Compose не используется | .local/platform.sqlite3 |
| Загрузки, отчёты, OIDC-ключ, локальная почта, initial-access | Том app_data → /app/.local | .local |
| Настройки | Закрытый файл --env-file на хосте | Окружение процесса |
| Логи приложения | docker compose logs | Окно запуска / .local/launcher.log |

Реальные имена Docker-томов имеют префикс проекта, например orenkod-local_postgres_data. Изменение -p создаёт другой набор томов: «пустая база» после смены имени проекта не означает, что старые данные исчезли.

OIDC-ключ .local/oidc должен сохраняться между перезапусками. Его замена нарушает проверку ранее подписанных токенов. Файлы загрузок и ключ не заменяются одной копией SQL.

## 9. Резервное копирование

### Docker / PostgreSQL

Создайте каталог backups на своём ПК. Имя копии ниже примерное: при повторении выберите новое; команда не перезаписывает существующий файл.

~~~text
docker compose --env-file .env.local -p orenkod-local exec app python manage.py backup_database --output /app/.local/backups/manual-backup.dump
docker compose --env-file .env.local -p orenkod-local cp app:/app/.local/backups/manual-backup.dump ./backups/manual-backup.dump
~~~

Создать каталог: PowerShell — New-Item -ItemType Directory -Force backups; Linux/macOS — mkdir -p backups.

Для полной копии дополнительно сохраните защищённые файлы из app_data, включая uploads и oidc. Для согласованной копии БД вместе с изменяемыми файлами используйте окно обслуживания, остановив бизнес-запись. Храните копии и настройки вне машины, с ограниченными правами; они содержат персональные данные и ключи.

### SQLite без Docker

В активированном окружении:

~~~bash
python manage.py backup_database --output .local/backups/manual-backup.sqlite3
~~~

Используется SQLite backup API, а не простое копирование открытого файла. Задайте новое имя при следующем запуске и перенесите копию в защищённое внешнее хранилище.

### Проверить восстановление PostgreSQL в новой пустой БД

Не направляйте восстановление сразу в рабочую БД. Пример на локальном стенде создаёт отдельную БД orenkod_restore_check:

~~~text
docker compose --env-file .env.local -p orenkod-local cp ./backups/manual-backup.dump db:/tmp/restore-check.dump
docker compose --env-file .env.local -p orenkod-local exec db createdb -U orenkod orenkod_restore_check
docker compose --env-file .env.local -p orenkod-local exec db pg_restore -U orenkod --no-owner --no-privileges --dbname orenkod_restore_check /tmp/restore-check.dump
docker compose --env-file .env.local -p orenkod-local exec db psql -U orenkod -d orenkod_restore_check -c "SELECT count(*) FROM auth_user;"
~~~

Если проверочная БД уже существует, используйте другое новое имя; не удаляйте существующие БД автоматически. После SQL-проверки отдельно подключите восстановленную БД к изолированной копии приложения и проверьте вход, роли, протокол, загрузки и OIDC/QR.

Для SQLite сначала проверьте копию через PRAGMA integrity_check. Останавливайте сервер/планировщик перед заменой рабочего файла, сохраните прежний файл отдельно, затем выполните migrate и функциональную проверку.

## 10. Обновление и возврат предыдущей версии

1. Зафиксируйте текущий commit/образ и сделайте копии БД, app_data и закрытых настроек.
2. Получите исходники: git pull --ff-only. Если есть локальные правки, сначала разберите их; не выполняйте reset --hard автоматически.
3. Остановите app и scheduler на время согласованного обслуживания.
4. Пересоберите и запустите:

~~~text
docker compose --env-file .env.local -p orenkod-local stop app scheduler
docker compose --env-file .env.local -p orenkod-local up --build -d
docker compose --env-file .env.local -p orenkod-local ps
docker compose --env-file .env.local -p orenkod-local logs --tail=100 app scheduler
~~~

Миграции применяются entrypoint до готовности приложения; scheduler ждёт healthy app.

Возврат старого исходного кода не откатывает БД. Если миграция несовместима, восстанавливайте согласованный комплект предыдущего образа, БД и файлов в изолированном окружении, проверяйте его и только затем переключайте рабочую установку. Универсальная команда «откатить все миграции» здесь не применяется.

Прежняя учебная .local/db.sqlite3 не подключается новой установкой и не удаляется. Для нужных старых данных выполните контролируемый перенос. Старые start-demo.bat/start-demo.sh остались совместимыми входами в тот же запуск без автоматических учебных записей.

## 11. Публичный сервер с HTTPS

Нужны собственный сервер, DNS-имя на его IP и доступные порты 80/443. Код не покупает хостинг/домен и не выдаёт публичную ссылку автоматически.

~~~bash
python3 scripts/configure_deployment.py --domain platform.your-domain.ru --output .env.production
docker compose --env-file .env.production -p orenkod-prod -f compose.yaml -f deployment/compose.production.yaml -f deployment/compose.public.yaml up --build -d
docker compose --env-file .env.production -p orenkod-prod -f compose.yaml -f deployment/compose.production.yaml -f deployment/compose.public.yaml exec app python manage.py check --deploy
~~~

Во всех последующих командах серверной установки повторяйте тот же --env-file, -p и весь набор -f. Не подменяйте серверный файл локальным.

Caddy принимает HTTPS, app/scheduler доверяют заголовкам только этого proxy. Настоящий DNS, сертификат, вход и внешняя доставка проверяются после размещения. Не направляйте публичный трафик на DEBUG=1/runserver. Подробности — [размещение](docs/deployment.md).

## 12. Типичные ошибки

| Симптом | Причина / действие |
| --- | --- |
| Python не найден | Установите Python 3.12+ с Add Python to PATH; откройте новый терминал |
| PowerShell запрещает Activate.ps1 | Используйте .venv\\Scripts\\python.exe напрямую, менять ExecutionPolicy не требуется |
| Docker daemon недоступен | Запустите Docker Desktop/Engine, проверьте Linux containers |
| Порт 8000 занят | Завершите свой прежний процесс либо измените FSP_PORT и адреса; чужие процессы лаунчер не завершает |
| redirect на HTTPS при локальном HTTP | Не используйте production override для локального ПК; создайте --local-конфигурацию |
| DisallowedHost | Проверьте DJANGO_ALLOWED_HOSTS, указываются hostname без схемы и порта |
| CSRF 403 | Проверьте фактический origin, порт и DJANGO_CSRF_TRUSTED_ORIGINS; не отключайте CSRF |
| Не работает вход старым demo-логином | Общих аккаунтов больше нет; начните с ministry и файла first-access.txt |
| first-access.txt отсутствует | Аккаунт мог быть создан с FSP_MINISTRY_PASSWORD; при повторном запуске файл не генерируется заново |
| Логин ministry уже занят старым аккаунтом | Он намеренно не повышается автоматически; используйте чистую установку либо согласованное другое FSP_MINISTRY_USERNAME |
| Пароль изменён в .env, вход не меняется | Bootstrap не сбрасывает существующий пароль. Для своего аккаунта используйте changepassword; федерации — через министерство |
| PostgreSQL password authentication failed после изменения .env | Пароль тома не меняется от правки переменной; верните корректный секрет или выполните согласованную смену роли БД. Не удаляйте том для «лечения» |
| После нового -p база кажется пустой | Выбран другой набор томов; проверьте имя проекта |
| Нет статики | Проверьте collectstatic, логи, правильный образ и доступ к app_data |
| Нет напоминаний/готового фонового отчёта | Проверьте scheduler и его логи; один раз запустите run_scheduler --once |
| Письма только локальные | SMTP не подключён; файловый backend не является внешним каналом |
| TLS/подписи пакетов не проходят при сборке | Передайте доверенный корпоративный CA/proxy; проверку TLS/подписей не отключайте |
| QR не открывается с телефона | localhost с телефона — сам телефон; для внешней проверки нужен доступный PUBLIC_BASE_URL |

Восстановить собственный пароль ministry оператор установки может через:

~~~text
docker compose --env-file .env.local -p orenkod-local exec app python manage.py changepassword ministry
~~~

Не отправляйте содержимое .env, first-access.txt и дампов в чат. Для диагностики используйте config --quiet: обычный docker compose config выводит раскрытые секреты.

## 13. Сборка за корпоративным proxy

Dockerfile поддерживает стандартные HTTP_PROXY/HTTPS_PROXY и BuildKit secret build_ca с доверенным CA bundle:

~~~bash
docker build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --secret id=build_ca,src=/path/to/trusted-ca.pem -t orenkod:local .
docker compose --env-file .env.local -p orenkod-local up -d
~~~

Переменные proxy должны быть заранее заданы в вашей оболочке. Если имя корпоративного proxy не разрешается внутри сборки, настройте доступный DNS/официальный --add-host для своего proxy; при необходимости согласованную host-сеть сборки. Не заменяйте доверенные сертификаты произвольными и не отключайте проверки. Секрет build_ca используется только при установке и не копируется в runtime-образ.

## 14. Проверки и рабочие разделы

После настройки:

~~~text
docker compose --env-file .env.local -p orenkod-local exec app python manage.py check
docker compose --env-file .env.local -p orenkod-local exec app python manage.py makemigrations --check --dry-run
docker compose --env-file .env.local -p orenkod-local exec app python manage.py test
~~~

Тесты используют отдельную БД и фиктивные данные. На сервере с ограниченной DB-ролью для тестов может требоваться отдельная роль/БД с правом CREATE DATABASE — не расширяйте рабочие права только ради теста.

Проверены 203 теста на SQLite и PostgreSQL; [CI программной версии](https://github.com/Markon123/pyton/actions/runs/40705685015) успешно проверил Windows, Ubuntu, PostgreSQL и Windows start.bat.

| Раздел | Путь внутри приложения |
| --- | --- |
| Министерство → федерации | /administration/federations/ |
| Соревнования и протоколы | /competitions/ |
| Реестр и организации | /registry/ |
| Свой профиль и спортивная история | /profile/ |
| Квалификации | /qualifications/ |
| Тренерские связи | /coaching/ |
| Рейтинг и сборная | /rating/ |
| Численность / сроки / фоновые документы | /reports/, /report-deadlines/, /reporting/ |
| Уведомления и проверка передачи | /notifications/, /notifications/mail/ |
| Аудит / собственные данные | /audit/, /privacy/ |
| OIDC / API | /identity/, /api/v1/, /integrations/ |
| Подготовка | /training/ |
| Апелляции, премии, министерство | /governance/ |
| Развитие, QR и судейские документы | /development/ |
| Навигатор без входа | /guide/ |

Техническая админка Django /admin/ отделена от бизнес-аккаунта министерства. Она не нужна для обычных бизнес-процессов; не заменяйте ей проверки и аудит кабинетов.

## 15. Документация и границы внедрения

[Изменения](docs/changes-2026-10-28.md), [матрица требований](docs/compliance.md), [архитектура](docs/architecture.md), [API](docs/api.md), [сценарии приёмки](docs/acceptance.md), [обращения по данным](docs/privacy.md), [материал первого чекпоинта](docs/checkpoint-1.md).

[Подробная видеодемонстрация с русской озвучкой](docs/video-tour.md): сборная, реестр и выгрузки, министерство, Д1–Д12 и проверка ролей.

Поддерживаются XLSX/DOCX/PDF. Образцы утверждённых бланков в присланных материалах отсутствуют, поэтому точное совпадение с официальной формой не подтверждено. Критерии подготовки/ЕВСК и суммы премий должны заполняться по действующим источникам. Программа не заменяет решение уполномоченного органа и не подтверждает юридическое соответствие одним фактом запуска.
