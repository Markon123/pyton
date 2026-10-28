"""Prepare fictional fixtures for the unchanged checkpoint-2 commit 953b71b.

Only .local/checkpoint2-270/*.sqlite3 is writable. The historical app is loaded
from a detached worktree and is never changed by this recording utility.
"""
import argparse
from contextlib import redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import subprocess
from urllib.parse import urlsplit


CURRENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT = CURRENT_ROOT.parent / 'pyton-checkpoint2-270'
FIXTURE_DIR = CURRENT_ROOT / '.local' / 'checkpoint2-270'
MAGIC = 'orenkod-checkpoint2-953b71b-fixture-v1'
SOURCE_REF = '953b71b'


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=str(FIXTURE_DIR / 'platform.sqlite3'))
    parser.add_argument('--base-url', default='http://127.0.0.1:8024')
    args = parser.parse_args()
    source_commit = subprocess.check_output(['git', '-C', str(PROJECT), 'rev-parse', 'HEAD'], text=True).strip()
    expected_commit = subprocess.check_output(['git', '-C', str(CURRENT_ROOT), 'rev-parse', SOURCE_REF], text=True).strip()
    if source_commit != expected_commit:
        parser.error('The detached source must be exactly checkpoint-2 commit 953b71b.')
    if subprocess.check_output(['git', '-C', str(PROJECT), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        parser.error('Historical tracked source must be unchanged.')
    allowed = FIXTURE_DIR.absolute()
    if allowed.resolve() != allowed:
        parser.error('The isolated fixture directory must not be a symbolic link.')
    target = Path(args.database).absolute()
    if target.parent != allowed or target.resolve().parent != allowed or target.suffix != '.sqlite3':
        parser.error('Only .local/checkpoint2-270/*.sqlite3 is allowed; the working databases are protected.')
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or not parsed.port or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        parser.error('Use an explicit loopback HTTP base URL with a port.')
    base_url = args.base_url.rstrip('/')
    allowed.mkdir(parents=True, exist_ok=True)
    marker_path = allowed / '.fixture-marker.json'
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    if target.exists() and marker.get('magic') != MAGIC:
        parser.error('An unmarked existing database is never modified.')
    if marker and marker.get('database') != str(target):
        parser.error('This directory is already bound to a different fixture database.')
    marker.update(magic=MAGIC, database=str(target))
    private_json(marker_path, marker)

    credentials_path = allowed / 'credentials.json'
    credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
    password = os.environ.get('CHECKPOINT270_PASSWORD') or credentials.get('password') or 'FspDemo2026!'
    if len(password) < 8:
        parser.error('The isolated fixture password must contain at least eight characters.')
    private_json(credentials_path, {'password': password, 'purpose': 'isolated fictional video accounts only'})
    os.environ['DATABASE_PATH'] = str(target)
    os.environ.pop('DATABASE_URL', None)
    os.environ['DJANGO_DEBUG'] = '1'
    os.environ['PUBLIC_BASE_URL'] = base_url
    os.environ['OIDC_PRIVATE_KEY_FILE'] = str(allowed / 'oidc' / 'private.pem')

    # Generated only in the ignored fixture directory; production config is unchanged.
    settings_source = (
        'import os\nfrom pathlib import Path\n'
        f"os.environ['DATABASE_PATH'] = {str(target)!r}\n"
        "os.environ.pop('DATABASE_URL', None)\n"
        f"os.environ['OIDC_PRIVATE_KEY_FILE'] = {str(allowed / 'oidc' / 'private.pem')!r}\n"
        "os.environ['DJANGO_DEBUG'] = '1'\n"
        'from config.settings import *\n'
        f'LOCAL_DIR = Path({str(allowed)!r})\n'
        "MEDIA_ROOT = LOCAL_DIR / 'uploads'\n"
        "STATIC_ROOT = LOCAL_DIR / 'static'\n"
        "EMAIL_FILE_PATH = LOCAL_DIR / 'mail'\n"
        "EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'\n"
        f'PUBLIC_BASE_URL = {base_url!r}\n'
        "ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']\n"
        'OIDC_ISSUER = PUBLIC_BASE_URL\n'
        'OAUTH2_PROVIDER = oauth_settings(LOCAL_DIR, DEBUG)\n'
    )
    (allowed / 'checkpoint270_settings.py').write_text(settings_source)
    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(allowed))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'checkpoint270_settings'
    import django
    django.setup()
    from django.conf import settings
    if Path(settings.DATABASES['default']['NAME']).resolve() != target or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
        raise RuntimeError('Fixture database guard failed.')
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.core.files.base import ContentFile
    from django.db import transaction
    from django.utils import timezone
    from docx import Document
    from federation.models import (Application, CoachLink, Competition, Consent, Discipline,
        JudgeAssignment, Organization, Profile,
        RankApplication, Result, Team)
    from training.models import (AssessmentNorm, AssessmentResult, Attendance, Enrollment,
        OrganizationProgram, Session, Stage, TrainingGroup)
    from development.models import RankCriterion
    from governance.models import Appeal, AwardPetition
    from oauth2_provider.models import get_application_model
    from identity.validator import DEMO_CLIENT_ID

    # Some historic fixture commands print their public fixture password; suppress it.
    captured = StringIO()
    with redirect_stdout(captured):
        call_command('migrate', interactive=False, verbosity=0, stdout=captured)
        if not marker.get('seeded'):
            call_command('seed_full_demo', verbosity=0, stdout=captured)
            marker['seeded'] = True
            private_json(marker_path, marker)

    today = timezone.localdate()
    year = today.year
    User = get_user_model()
    with transaction.atomic():
        ministry = User.objects.get(username='ministry')
        federation = User.objects.get(username='federation')
        athlete = User.objects.get(username='athlete').profile
        athlete2 = User.objects.get(username='demo_athlete_06').profile
        coach = User.objects.get(username='coach').profile
        judge = User.objects.get(username='judge').profile
        for user in User.objects.all():
            user.set_password(password)
            user.save(update_fields=['password'])
            Consent.objects.get_or_create(user=user, kind='personal_data', version='1.0')
        discipline = Discipline.objects.get(code='algorithm')
        for person in (athlete, athlete2, coach, judge):
            person.disciplines.add(discipline)
        for person in (athlete, athlete2):
            CoachLink.objects.update_or_create(coach=coach, athlete=person, discipline=discipline, defaults={'status': 'active'})
        published = Competition.objects.filter(protocol_published=True, disciplines=discipline).order_by('starts_on').first()
        for competition in Competition.objects.filter(protocol_published=True, disciplines=discipline):
            JudgeAssignment.objects.get_or_create(competition=competition, judge=judge, defaults={'position': 'Главный судья'})
        main_result = Result.objects.filter(application__athlete=athlete, application__competition=published, application__discipline=discipline).first()
        second_result = Result.objects.filter(application__athlete=athlete2, application__competition=published, application__discipline=discipline).first()
        if not (main_result and second_result):
            raise RuntimeError('Published athlete results are missing from the fixture.')
        main_result.score = Decimal('142.50')
        main_result.save(update_fields=['score'])
        second_result.score = Decimal('120.00')
        second_result.save(update_fields=['score'])
        open_competition = Competition.objects.filter(state='open', disciplines=discipline).order_by('starts_on').first()

        # Reproduce the final state visible in the submitted checkpoint-2 clip.
        # This is explicitly fictional fixture data, not a migration or app fix.
        checkpoint, _ = Competition.objects.get_or_create(title='Кубок ОренКод · чекпоинт 2', defaults={
            'description': 'Демонстрационный турнир из сданной записи чекпоинта № 2. Все сведения вымышлены.',
            'starts_on': date(2026, 10, 28), 'ends_on': date(2026, 10, 28),
            'registration_opens': date(2026, 10, 21), 'registration_closes': date(2026, 10, 28),
            'location': 'Оренбург · вымышленная демонстрационная площадка', 'format': 'online',
            'level': 'regional', 'calendar_plan': 'regional', 'plan_number': 'ДЕМО-ЧП2-001',
            'age_min': 12, 'age_max': 99, 'state': 'finished', 'protocol_published': True})
        checkpoint.disciplines.add(discipline)
        checkpoint_application, _ = Application.objects.get_or_create(
            competition=checkpoint, athlete=athlete, discipline=discipline,
            defaults={'submitted_by': athlete.user, 'status': 'approved'})
        checkpoint_result, _ = Result.objects.get_or_create(application=checkpoint_application,
            defaults={'place': 1, 'score': Decimal('142.50')})
        JudgeAssignment.objects.get_or_create(competition=checkpoint, judge=judge,
            defaults={'position': 'Главный судья'})

        imported, _ = Competition.objects.get_or_create(title='Турнир «Обмен результатами» · видеопоказ', defaults={
            'description': 'Изолированный тест импорта; все сведения вымышлены.',
            'starts_on': today - timedelta(days=1), 'ends_on': today - timedelta(days=1),
            'registration_opens': today - timedelta(days=10), 'registration_closes': today - timedelta(days=2),
            'location': 'Оренбург', 'format': 'online', 'level': 'regional', 'calendar_plan': 'regional',
            'plan_number': f'ТЕСТ-{year}-099', 'age_min': 12, 'age_max': 99, 'state': 'closed'})
        imported.disciplines.add(discipline)
        imported_apps = []
        for person in (athlete, athlete2):
            app, _ = Application.objects.get_or_create(competition=imported, athlete=person, discipline=discipline,
                defaults={'submitted_by': federation, 'status': 'approved'})
            imported_apps.append(app)
        JudgeAssignment.objects.get_or_create(competition=imported, judge=judge, defaults={'position': 'Главный судья'})

        organization = coach.organization
        for stage in Stage.values:
            OrganizationProgram.objects.get_or_create(organization=organization, discipline=discipline, stage=stage,
                defaults={'description': 'Тестовая программа для записи процесса подготовки.', 'license_details': 'Условные реквизиты для видеопоказа'})
        group, _ = TrainingGroup.objects.get_or_create(name='Алгоритмы · начальная группа', defaults={
            'organization': organization, 'discipline': discipline, 'stage': Stage.INITIAL, 'coach': coach})
        next_group, _ = TrainingGroup.objects.get_or_create(name='Алгоритмы · учебно-тренировочная группа', defaults={
            'organization': organization, 'discipline': discipline, 'stage': Stage.TRAINING, 'coach': coach})
        lesson, _ = Session.objects.get_or_create(group=group, topic='Разбор алгоритмических задач', defaults={
            'date': today - timedelta(days=1), 'format': 'offline', 'created_by': coach.user})
        norms = []
        for name, unit, threshold, direction in [('Скорость печати', 'знаков/мин', '200', 'gte'), ('Специальное алгоритмическое задание', 'сек', '90', 'lte')]:
            norm, _ = AssessmentNorm.objects.get_or_create(discipline=discipline, stage=Stage.INITIAL, name=name,
                defaults={'sex': 'all', 'unit': unit, 'threshold': Decimal(threshold), 'direction': direction,
                    'source': 'Условный порог для видеопоказа; не норматив ФССП.', 'demonstration': True})
            norms.append(norm)
        enrollments = []
        for person in (athlete, athlete2):
            enrollment = Enrollment.objects.filter(athlete=person, group__discipline=discipline).first()
            if not enrollment:
                enrollment = Enrollment(athlete=person, group=group, start_date=date(year - 2, 1, 1),
                    municipality=person.municipality or organization.municipality, sex='male')
                enrollment.full_clean()
                enrollment.save()
            enrollments.append(enrollment)
            Attendance.objects.get_or_create(session=lesson, athlete=person, defaults={'present': True})
            for norm, value in zip(norms, (Decimal('245'), Decimal('74'))):
                if not AssessmentResult.objects.filter(athlete=person, norm=norm).exists():
                    assessed = AssessmentResult(athlete=person, norm=norm, value=value, tested_on=lesson.date, recorded_by=coach.user)
                    assessed.full_clean()
                    assessed.save()
        RankCriterion.objects.get_or_create(discipline=discipline, rank_title='I разряд', is_demo=True,
            source_text='Условный критерий видеопоказа; не основание официального присвоения.',
            defaults={'levels': ['regional', 'interregional'], 'max_place': 3, 'min_score': 100, 'age_min': 12, 'age_max': 99})
        # Use an untouched result belonging to the main athlete, so the recording
        # can submit the appeal without logging into a different athlete account.
        appealable_result = Result.objects.filter(application__athlete=athlete,
            application__competition__protocol_published=True, appeals__isnull=True).first() or main_result

        oidc_app = get_application_model().objects.filter(client_id=DEMO_CLIENT_ID).first()
        if oidc_app:
            oidc_app.redirect_uris = base_url + '/identity/demo/callback/'
            oidc_app.save(update_fields=['redirect_uris'])

        document = Document()
        document.add_heading('Положение о соревновании', 0)
        document.add_paragraph('Тестовый документ для записи работы платформы. Все сведения вымышлены.')
        document.add_paragraph('Участие после подачи заявки и одобрения федерацией; результаты учитываются по дисциплинам.')
        stream = BytesIO()
        document.save(stream)
        (allowed / 'regulation.docx').write_bytes(stream.getvalue())
        for competition in (published, checkpoint, open_competition, imported):
            if not competition.regulation:
                competition.regulation.save(f'video-{competition.pk}.docx', ContentFile(stream.getvalue()), save=True)

    manifest = {
        'fictional': True, 'database': str(target), 'base_url': base_url,
        'source_ref': SOURCE_REF, 'source_commit': source_commit, 'source_worktree': str(PROJECT),
        'settings_module': 'checkpoint270_settings', 'pythonpath': str(allowed),
        'users': {role: {'username': person.user.username, 'profile_id': person.pk, 'name': person.display_name}
            for role, person in [('federation', federation.profile), ('athlete', athlete), ('athlete2', athlete2),
                                 ('coach', coach), ('judge', judge), ('ministry', ministry.profile)]},
        'ids': {'athlete': athlete.pk, 'athlete2': athlete2.pk, 'coach': coach.pk, 'judge': judge.pk,
            'discipline': discipline.pk, 'published_competition': published.pk,
            'open_competition': open_competition.pk, 'import_competition': imported.pk,
            'import_applications': [app.pk for app in imported_apps], 'import_application': imported_apps[0].pk, 'group': group.pk,
            'next_group': next_group.pk, 'target_group': next_group.pk, 'enrollment': enrollments[0].pk, 'session': lesson.pk,
            'result': checkpoint_result.pk, 'appealable_result': appealable_result.pk,
            'checkpoint_competition': checkpoint.pk, 'checkpoint_result': checkpoint_result.pk,
            'newresult': checkpoint_result.pk,
            'team': Team.objects.filter(discipline=discipline, season=year).values_list('pk', flat=True).first(),
            'organization': organization.pk, 'norms': [n.pk for n in norms], 'norm': norms[0].pk,
            'award_petition': AwardPetition.objects.filter(athlete=athlete, year=year).values_list('pk', flat=True).first()},
        'year': year, 'today': str(today), 'regulation': str(allowed / 'regulation.docx'),
        'credentials_file': str(credentials_path),
        'files': {'results_json': str(allowed / 'results.json'), 'results_csv': str(allowed / 'results.csv'),
                  'regulation': str(allowed / 'regulation.docx')},
    }
    private_json(allowed / 'manifest.json', manifest)
    import_payload = {'results': [{'application': app.pk, 'place': place, 'score': score}
        for app, place, score in zip(imported_apps, (1, 2), ('155.50', '130.00'))]}
    private_json(allowed / 'results.json', import_payload)
    (allowed / 'results.csv').write_text('application,place,score\n' + '\n'.join(
        f"{row['application']},{row['place']},{row['score']}" for row in import_payload['results']) + '\n')
    # A consistent baseline lets the recorder retry without modifying working data.
    from django.db import connections
    connections.close_all()
    with sqlite3.connect(target) as source, sqlite3.connect(allowed / 'baseline.sqlite3') as destination:
        source.backup(destination)
    print('Isolated fictional video fixtures ready; manifest contains IDs only. Password is not printed.')
    print('DJANGO_SETTINGS_MODULE=checkpoint270_settings; PYTHONPATH=' + str(allowed))


if __name__ == '__main__':
    main()
