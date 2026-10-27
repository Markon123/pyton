import os
from pathlib import Path
import tempfile
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from federation.documents import build_document
from federation.services import census, census_breakdown, layout_document
from .forms import validate_saved_params
from .models import ReportJob


LEASE = timedelta(minutes=5)


def report_directory():
    path = Path(getattr(settings, 'LOCAL_DIR', Path(settings.BASE_DIR) / '.local')) / 'reports'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def expected_filename(job):
    if not job.claim_token or job.format not in ('xlsx', 'docx', 'pdf'):
        raise ValidationError('Некорректный файл отчёта.')
    return f'{job.number}-{job.claim_token.hex}.{job.format}'


def report_path(job):
    filename = expected_filename(job)
    if job.filename != filename:
        raise ValidationError('Имя файла отчёта не совпадает с выданной задачей.')
    directory = report_directory().resolve()
    path = directory / filename
    if path.resolve().parent != directory:
        raise ValidationError('Файл находится вне каталога отчётов.')
    return path


def build_census(job):
    validate_saved_params(job.params, job.format)
    p = job.params
    extra = {'age_min': p['age_min'], 'age_max': p['age_max'], 'municipality': p['municipality']}
    counts = census(p['year_from'], p['year_to'], p['discipline'], p['organization'], **extra)
    split = census_breakdown(p['year_from'], p['year_to'], p['discipline'], p['organization'], **extra)
    rows = [[r['year'], 'Всего', 'Регион', r['total']] for r in counts]
    rows += [[r['year'], r['dimension'], r['label'], r['total']] for r in split]
    title, subtitle = layout_document('census', 'Динамика численности занимающихся',
        'Фоновый отчёт. Источник: интервалы зачисления в группы; для периодов до начала истории — интервалы реестра. '
        'Возраст на 31 декабря отчётного года. Итог считает уникальных спортсменов; разрезы могут пересекаться.')
    # build_document creates document bytes inside this process; no HTTP request or server is used.
    response = build_document('census', job.format, title, ['Год', 'Разрез', 'Группа', 'Число спортсменов'], rows, subtitle)
    return response.content


def due_jobs(now):
    return ReportJob.objects.filter(Q(status='queued') | Q(status='running', lease_expires_at__lte=now)).order_by('created_at', 'pk')


def process_reports(limit=5):
    now = timezone.now()
    ids = list(due_jobs(now).values_list('pk', flat=True)[:limit])
    completed = 0
    for pk in ids:
        token = uuid.uuid4()
        now = timezone.now()
        # Compare-and-set is portable across SQLite/PostgreSQL and avoids claiming
        # a task twice without holding a database transaction during document generation.
        changed = due_jobs(now).filter(pk=pk).update(status='running', started_at=now, completed_at=None,
                    lease_expires_at=now + LEASE, claim_token=token, filename='', error='')
        if not changed:
            continue
        job = ReportJob.objects.get(pk=pk)
        temporary = None
        destination = None
        accepted = False
        try:
            content = build_census(job)
            directory = report_directory()
            destination = directory / expected_filename(job)
            # Each lease writes a distinct claim filename. A stale worker cannot
            # overwrite the ready file of a newer lease; only a successful CAS publishes it.
            with tempfile.NamedTemporaryFile(dir=directory, prefix=f'.{job.number}-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
            temporary = None
            changed = ReportJob.objects.filter(pk=pk, status='running', claim_token=token).update(
                status='ready', completed_at=timezone.now(), lease_expires_at=None, filename=destination.name, error='')
            accepted = bool(changed)
            completed += int(accepted)
        except Exception as error:
            # Never persist arbitrary exception text, query strings or filesystem details.
            ReportJob.objects.filter(pk=pk, status='running', claim_token=token).update(
                status='failed', completed_at=timezone.now(), lease_expires_at=None,
                error=f'{type(error).__name__}: не удалось сформировать отчёт. Проверьте параметры и повторите задачу.')
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            if destination and not accepted:
                destination.unlink(missing_ok=True)
    return completed
