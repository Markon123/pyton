from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from federation.access import roles_required
from federation.services import audit
from .forms import CensusReportForm, DocumentReportForm
from .models import ReportJob, REPORT_KINDS
from .services import report_path


def own_job(request, number):
    return get_object_or_404(ReportJob, number=number, user=request.user)


def index_context(request, form, kind):
    return {'jobs': ReportJob.objects.filter(user=request.user)[:100], 'form': form,
            'kinds': REPORT_KINDS, 'selected_kind': kind,
            'kind_label': dict(REPORT_KINDS)[kind]}


@roles_required('federation')
@require_safe
def index(request):
    kind = request.GET.get('kind', 'census')
    if kind not in dict(REPORT_KINDS):
        raise Http404('Неизвестный вид отчёта.')
    initial = {'format': request.GET.get('fmt', 'xlsx')}
    if kind == 'census':
        form = CensusReportForm(initial=initial)
    else:
        initial.update({'kind': kind, 'object_id': request.GET.get('object_id')})
        form = DocumentReportForm(kind=kind, initial=initial)
    return render(request, 'reporting/index.html', index_context(request, form, kind))


@roles_required('federation')
@require_POST
def create(request):
    kind = request.POST.get('kind', 'census')
    if kind not in dict(REPORT_KINDS):
        raise Http404('Неизвестный вид отчёта.')
    form = CensusReportForm(request.POST) if kind == 'census' else DocumentReportForm(request.POST, kind=kind)
    if not form.is_valid():
        return render(request, 'reporting/index.html', index_context(request, form, kind), status=400)
    job = ReportJob(user=request.user, params=form.parameters(), format=form.cleaned_data['format'])
    job.full_clean()
    job.save()
    audit(request, 'Поставлен фоновый отчёт', f'report:{job.number}', f'{job.get_kind_display()} · {job.format}')
    return redirect('reporting:job', number=job.number)


@roles_required('federation')
@require_safe
def job(request, number):
    record = own_job(request, number)
    if 'application/json' in request.headers.get('Accept', ''):
        response = JsonResponse({'id': str(record.number), 'kind': record.kind, 'status': record.status, 'error': record.error,
            'created_at': record.created_at.isoformat(),
            'download_url': reverse('reporting:download', args=[record.number]) if record.status == 'ready' else None})
        response['Cache-Control'] = 'private, no-store'
        return response
    return render(request, 'reporting/job.html', {'job': record})


@roles_required('federation')
@require_safe
def download(request, number):
    record = own_job(request, number)
    if record.status != 'ready':
        raise Http404('Отчёт ещё не готов.')
    try:
        path = report_path(record)
        stream = path.open('rb')
    except (ValidationError, FileNotFoundError):
        raise Http404('Файл отчёта отсутствует.')
    response = FileResponse(stream, as_attachment=True, filename=f'{record.kind}-{record.number}.{record.format}')
    response['Cache-Control'] = 'private, no-store'
    return response
