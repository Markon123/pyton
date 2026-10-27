from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from federation.access import active_role, managed_profiles, role_profiles, roles_required
from federation.documents import build_document
from federation.models import Competition, Discipline, JudgeAssignment, Organization, Profile, Result
from federation.services import audit, census, integer, layout_document, notify
from .forms import CertificateForm, CriterionForm, EvidenceForm
from .models import Evidence, ParticipationCertificate, RankCriterion, result_fingerprint
from .services import can_access_result, certificate_pdf, certificate_url, rank_candidates


def person_for(request):
    role = active_role(request)
    if role not in ('federation', 'athlete', 'coach'):
        raise PermissionDenied('Личный путь доступен спортсмену, его тренеру и федерации.')
    people = role_profiles('athlete').filter(pk__in=managed_profiles(request)).select_related('user', 'organization')
    pk = request.GET.get('athlete')
    person = get_object_or_404(people, pk=pk) if pk else people.first()
    return people, person


def results_for_request(request, *, include_ineligible=False):
    from governance.services import valid_results
    role = active_role(request)
    qs = Result.objects.filter(application__status='approved', application__competition__protocol_published=True).select_related(
        'application__athlete__user', 'application__competition', 'application__discipline')
    if not include_ineligible:
        qs = valid_results(qs)
    if role == 'federation':
        return qs
    if role == 'athlete':
        return qs.filter(application__athlete__user=request.user)
    if role == 'coach':
        return qs.filter(pk__in=[r.pk for r in qs if can_access_result(request, r)])
    return qs.none()


@roles_required('federation', 'ministry')
@require_safe
def index(request):
    year = integer(request.GET.get('year'), timezone.localdate().year, 1900, timezone.localdate().year)
    counts = census(year - 1, year)
    competitions = Competition.objects.filter(starts_on__year=year, starts_on__lte=timezone.localdate(), state='finished').exclude(plan_number='').prefetch_related('disciplines')
    organizations = Organization.objects.all()
    try:
        from training.models import OrganizationProgram
        programs = OrganizationProgram.objects.select_related('organization', 'discipline')
    except ImportError:
        programs = []
    return render(request, 'development/index.html', {'year': year, 'counts': counts, 'competitions': competitions,
                  'organizations': organizations, 'programs': programs, 'evidence': Evidence.objects.filter(year=year),
                  'levels': competitions.values('level').annotate(total=Count('pk')).order_by('level'),
                  'form': EvidenceForm(initial={'year': year}) if active_role(request) == 'federation' else None})


@roles_required('federation', 'athlete', 'coach')
@require_safe
def pathway(request):
    people, person = person_for(request)
    upcoming = Competition.objects.none()
    matches = []
    current = None
    if person:
        current = person.current_rank
        ids = []
        for comp in Competition.objects.filter(state__in=['open', 'published'], starts_on__gte=timezone.localdate()).prefetch_related('disciplines'):
            age = person.age_on(comp.starts_on)
            if age is not None and comp.age_min <= age <= comp.age_max and person.disciplines.filter(pk__in=comp.disciplines.values('pk')).exists():
                ids.append(comp.pk)
        upcoming = Competition.objects.filter(pk__in=ids)
        matches = rank_candidates([person.pk])
    return render(request, 'development/pathway.html', {'people': people, 'person': person, 'current': current,
                  'upcoming': upcoming, 'matches': matches, 'criteria': RankCriterion.objects.filter(active=True, discipline__profiles=person).distinct() if person else []})


@roles_required('federation', 'coach')
@require_safe
def candidates(request):
    matches = rank_candidates(managed_profiles(request).values_list('pk', flat=True))
    return render(request, 'development/candidates.html', {'matches': matches})


@roles_required('federation')
def criteria(request):
    record = None
    if request.GET.get('edit'):
        record = get_object_or_404(RankCriterion, pk=request.GET['edit'])
    form = CriterionForm(request.POST or None, instance=record)
    if request.method == 'POST' and form.is_valid():
        record = form.save()
        audit(request, 'Изменён критерий кандидатов на разряд', f'criterion:{record.pk}', str(record))
        messages.success(request, 'Критерий сохранён. Автоматическая подсветка не заменяет рассмотрение федерацией.')
        return redirect('development:criteria')
    return render(request, 'development/criteria.html', {'records': RankCriterion.objects.select_related('discipline'), 'form': form}, status=400 if request.method == 'POST' else 200)


@require_POST
@roles_required('federation')
def evidence_create(request):
    form = EvidenceForm(request.POST, request.FILES)
    if form.is_valid():
        evidence = form.save(commit=False)
        evidence.created_by = request.user
        evidence.save()
        audit(request, 'Прикреплено подтверждение показателя', f'evidence:{evidence.pk}', evidence.get_indicator_display())
        return redirect(f'{reverse("development:index")}?year={evidence.year}')
    return render(request, 'development/form.html', {'form': form, 'title': 'Подтверждение показателя'}, status=400)


@roles_required('federation', 'ministry')
@require_safe
def evidence_download(request, pk):
    record = get_object_or_404(Evidence, pk=pk)
    if not record.document:
        raise Http404
    try:
        response = FileResponse(record.document.open('rb'), as_attachment=True, filename=record.document.name.rsplit('/', 1)[-1])
        response['Cache-Control'] = 'private, no-store'
        return response
    except FileNotFoundError:
        raise Http404('Файл отсутствует в хранилище.')


@roles_required('federation', 'athlete', 'coach')
@require_safe
def certificates(request):
    results = results_for_request(request)
    form = CertificateForm(results=results)
    certificates = ParticipationCertificate.objects.filter(result__in=results_for_request(request, include_ineligible=True)).select_related(
        'result__application__athlete__user', 'result__application__competition', 'result__application__discipline')
    return render(request, 'development/certificates.html', {'form': form, 'certificates': certificates})


@require_POST
@roles_required('federation', 'athlete', 'coach')
@transaction.atomic
def certificate_issue(request):
    form = CertificateForm(request.POST, results=results_for_request(request))
    if not form.is_valid():
        return render(request, 'development/form.html', {'form': form, 'title': 'Справка об участии'}, status=400)
    result = form.cleaned_data['result']
    result = Result.objects.select_for_update().select_related('application__competition', 'application__athlete__user', 'application__discipline').get(pk=result.pk)
    # Issuing a new copy explicitly invalidates old public numbers for that result.
    ParticipationCertificate.objects.filter(result=result, revoked=False).update(revoked=True)
    app = result.application
    snapshot = {'athlete_name': app.athlete.display_name, 'competition_title': app.competition.title,
                'competition_date': app.competition.starts_on.isoformat(), 'discipline_name': app.discipline.name,
                'place': result.place, 'score': str(result.score)}
    record = ParticipationCertificate(result=result, issued_by=request.user, public_consent=True,
                                      fingerprint=result_fingerprint(result), snapshot=snapshot)
    record.full_clean()
    record.save()
    audit(request, 'Выдана справка с QR', f'certificate:{record.pk}', str(record.number))
    notify(result.application.athlete.user, 'Справка об участии готова', result.application.competition.title, reverse('development:certificates'))
    return redirect('development:certificate_download', number=record.number)


@roles_required('federation', 'athlete', 'coach')
@require_safe
def certificate_download(request, number):
    record = get_object_or_404(ParticipationCertificate.objects.select_related(
        'result__application__athlete__user', 'result__application__competition', 'result__application__discipline'), number=number)
    if not can_access_result(request, record.result):
        raise PermissionDenied
    return certificate_pdf(record, request)


@require_POST
@roles_required('federation', 'athlete', 'coach')
def certificate_revoke(request, number):
    record = get_object_or_404(ParticipationCertificate.objects.select_related('result__application__athlete__user', 'result__application__discipline'), number=number)
    if not can_access_result(request, record.result):
        raise PermissionDenied
    record.revoked = True
    record.save(update_fields=['revoked'])
    audit(request, 'Отозвана справка', f'certificate:{record.pk}', str(number))
    return redirect('development:certificates')


def verify(request, number):
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseBadRequest('Проверка доступна только для чтения.')
    record = get_object_or_404(ParticipationCertificate.objects.select_related(
        'result__application__athlete__user', 'result__application__competition', 'result__application__discipline'), number=number)
    # This page deliberately receives no model or full name that could expose private fields.
    app = record.result.application
    snapshot = record.snapshot
    response = render(request, 'development/verify.html', {'number': str(record.number), 'valid': record.valid,
                      'initials': record.public_initials, 'competition_title': snapshot.get('competition_title', app.competition.title),
                      'competition_date': snapshot.get('competition_date', app.competition.starts_on.isoformat()),
                      'discipline_name': snapshot.get('discipline_name', app.discipline.name),
                      'place': snapshot.get('place', record.result.place), 'score': snapshot.get('score', str(record.result.score))})
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@roles_required('federation', 'judge', 'ministry')
@require_safe
def judging(request):
    role = active_role(request)
    assignments = JudgeAssignment.objects.select_related('judge__user', 'competition')
    if role == 'judge':
        assignments = assignments.filter(judge__user=request.user)
    elif role == 'ministry':
        assignments = assignments.exclude(competition__state='draft')
    return render(request, 'development/judging.html', {'assignments': assignments,
                  'judges': role_profiles('judge') if role == 'federation' else [], 'can_export_history': role in ('federation', 'judge')})


@roles_required('federation', 'judge', 'ministry')
@require_safe
def judging_document(request, kind, pk, fmt):
    role = active_role(request)
    if kind == 'panel':
        comp = get_object_or_404(Competition, pk=pk)
        if role == 'judge' and not comp.judge_assignments.filter(judge__user=request.user).exists():
            raise PermissionDenied
        if role == 'ministry' and comp.state == 'draft':
            raise PermissionDenied
        assignments = comp.judge_assignments.select_related('judge__user')
        title = f'Состав судейской коллегии · {comp.title}'
        headers = ['Судья', 'Должность', 'Категория на дату старта', 'Дата соревнования']
        rows = []
        for assignment in assignments:
            category = assignment.judge.rank_applications.filter(kind='category', status='approved',
                        assigned_on__lte=comp.starts_on, valid_until__gte=comp.starts_on).order_by('-assigned_on', '-id').first()
            rows.append([assignment.judge.display_name, assignment.position, category.requested_title if category else 'Не указана', comp.starts_on])
        subtitle = 'Источник: назначения судейской коллегии в карточке соревнования.'
    elif kind == 'history':
        person = get_object_or_404(role_profiles('judge'), pk=pk)
        if role != 'federation' and not (role == 'judge' and person.user_id == request.user.pk):
            raise PermissionDenied
        assignments = person.judge_assignments.filter(competition__state='finished', competition__ends_on__lte=timezone.localdate()).select_related('competition')
        title, headers = f'История судейства · {person.display_name}', ['Дата', 'Соревнование', 'Уровень', 'Должность']
        rows = [[a.competition.starts_on, a.competition.title, a.competition.get_level_display(), a.position] for a in assignments]
        subtitle = 'Источник: история судейских назначений платформы. Фактическое выполнение работы подтверждается протоколами.'
    else:
        raise Http404
    title, subtitle = layout_document(f'judging-{kind}', title, subtitle)
    return build_document(f'judging-{kind}', fmt, title, headers, rows, subtitle)
