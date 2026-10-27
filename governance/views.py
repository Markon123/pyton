from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from federation.access import active_role, managed_profiles, roles_required
from federation.documents import build_document
from federation.models import Competition, Organization, Team
from federation.services import audit, census, integer, notify
from .forms import AppealForm, AppealReviewForm, AwardRuleForm, PetitionTransitionForm, ViolationForm
from .models import Appeal, AwardPetition, AwardRule, ViolationRecord
from .services import award_candidates, may_appeal, prepare_petition, record_violation, review_appeal


def form_page(request, form, title, status=200):
    return render(request, 'federation/form.html', {'form': form, 'title': title, 'submit_label': 'Сохранить', 'cancel_url': reverse('governance:appeals')}, status=status)


def visible_appeals(request):
    records = Appeal.objects.select_related('result__application__athlete__user', 'result__application__competition', 'violation__athlete__user', 'violation__competition', 'submitted_by', 'reviewed_by')
    if active_role(request) != 'federation':
        allowed = [a.pk for a in records if may_appeal(request, a.athlete, a.competition, a.result.application.discipline if a.result_id else None)]
        records = records.filter(pk__in=allowed)
    return records


@roles_required('federation', 'athlete', 'coach')
def appeals(request):
    role = active_role(request)
    form = AppealForm(request.POST or None, request.FILES or None, request=request) if role != 'federation' else None
    if request.method == 'POST':
        if role == 'federation':
            raise PermissionDenied
        if form.is_valid():
            record = form.save()
            audit(request, 'Подана апелляция', f'appeal:{record.pk}', record.reason)
            from federation.access import role_profiles
            for person in role_profiles('federation'):
                notify(person.user, 'Новая апелляция', f'{record.athlete}: {record.competition}', reverse('governance:appeal_detail', args=[record.pk]))
            return redirect('governance:appeal_detail', pk=record.pk)
    violations = ViolationRecord.objects.select_related('athlete__user', 'competition')
    if role != 'federation':
        violations = violations.filter(athlete__in=managed_profiles(request))
        if role == 'coach':
            violations = violations.filter(pk__in=[v.pk for v in violations if may_appeal(request, v.athlete, v.competition)])
    return render(request, 'governance/appeals.html', {'appeals': visible_appeals(request), 'violations': violations, 'form': form, 'federated': role == 'federation'}, status=400 if request.method == 'POST' else 200)


@roles_required('federation', 'athlete', 'coach')
def appeal_detail(request, pk):
    appeal = get_object_or_404(visible_appeals(request), pk=pk)
    form = AppealReviewForm(request.POST or None) if active_role(request) == 'federation' else None
    if request.method == 'POST':
        if form is None:
            raise PermissionDenied
        if form.is_valid():
            try:
                review_appeal(request, appeal, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, 'Решение сохранено. История изменения протокола сохранена.')
                return redirect('governance:appeal_detail', pk=pk)
    amendments = appeal.competition.amendments.select_related('amended_by', 'appeal')
    if active_role(request) != 'federation':
        amendments = amendments.filter(appeal=appeal)
    return render(request, 'governance/appeal_detail.html', {'appeal': appeal, 'form': form, 'amendments': amendments}, status=400 if request.method == 'POST' else 200)


@roles_required('federation')
def violation_create(request):
    form = ViolationForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        record_violation(request, form.save(commit=False))
        messages.success(request, 'Решение сохранено. Спортсмен уведомлён о праве на апелляцию.')
        return redirect('governance:appeals')
    return form_page(request, form, 'Зафиксировать нарушение', 400 if request.method == 'POST' else 200)


@roles_required('federation', 'athlete', 'coach')
def evidence(request, kind, pk):
    if kind == 'appeal':
        obj = get_object_or_404(visible_appeals(request), pk=pk)
    elif kind == 'violation':
        obj = get_object_or_404(ViolationRecord, pk=pk)
        if active_role(request) != 'federation' and not may_appeal(request, obj.athlete, obj.competition):
            raise PermissionDenied
    else:
        raise Http404
    if not obj.evidence_file:
        raise Http404
    try:
        return FileResponse(obj.evidence_file.open('rb'), as_attachment=True, filename=obj.evidence_file.name.rsplit('/', 1)[-1])
    except FileNotFoundError:
        raise Http404


@roles_required('federation', 'athlete')
def awards(request):
    year = integer(request.GET.get('year'), timezone.localdate().year, 1900, timezone.localdate().year)
    candidates = award_candidates(year) if active_role(request) == 'federation' else []
    petitions = AwardPetition.objects.filter(year=year).select_related('athlete__user', 'result__application__competition', 'rule')
    if active_role(request) == 'athlete':
        petitions = petitions.filter(athlete__user=request.user)
    return render(request, 'governance/awards.html', {'year': year, 'candidates': candidates, 'petitions': petitions, 'rules': AwardRule.objects.all(), 'deadline': date(year + 1, 2, 1), 'federated': active_role(request) == 'federation'})


@require_POST
@roles_required('federation')
def petition_prepare(request):
    year = integer(request.POST.get('year'), timezone.localdate().year, 1900, timezone.localdate().year)
    athlete_id = integer(request.POST.get('athlete'), 0)
    try:
        petition = prepare_petition(request, year, athlete_id)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('governance:awards')
    return redirect('governance:petition_detail', pk=petition.pk)


@roles_required('federation', 'athlete')
def petition_detail(request, pk):
    petition = get_object_or_404(AwardPetition.objects.select_related('athlete__user', 'result__application__competition', 'rule'), pk=pk)
    if active_role(request) != 'federation' and petition.athlete.user_id != request.user.pk:
        raise PermissionDenied
    form = PetitionTransitionForm(request.POST or None, instance=petition) if active_role(request) == 'federation' else None
    if request.method == 'POST':
        if form is None:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            audit(request, 'Обновлено ходатайство', f'petition:{pk}', petition.get_status_display())
            notify(petition.athlete.user, 'Статус ходатайства', petition.get_status_display(), reverse('governance:petition_detail', args=[pk]))
            return redirect('governance:petition_detail', pk=pk)
    return render(request, 'governance/petition_detail.html', {'petition': petition, 'form': form}, status=400 if request.method == 'POST' else 200)


@roles_required('federation')
def award_rule(request, pk=None):
    instance = get_object_or_404(AwardRule, pk=pk) if pk else None
    form = AwardRuleForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        rule = form.save()
        audit(request, 'Настроены правила премирования', f'awardrule:{rule.pk}', str(rule))
        return redirect('governance:awards')
    return form_page(request, form, 'Правило премирования', 400 if request.method == 'POST' else 200)


@roles_required('federation', 'athlete')
def petition_document(request, pk, fmt):
    petition = get_object_or_404(AwardPetition.objects.select_related('athlete__user', 'result__application__competition', 'rule'), pk=pk)
    if active_role(request) != 'federation' and petition.athlete.user_id != request.user.pk:
        raise PermissionDenied
    rows = [['Спортсмен', petition.athlete.display_name], ['Год результатов', petition.year], ['Сумма, руб.', petition.amount],
        ['Состояние', petition.get_status_display()], ['Основание', petition.result.application.competition.title], ['Место', petition.result.place],
        ['Правило на момент подготовки', str(petition.provenance)], ['Паспорт', petition.passport_mask], ['ИНН', petition.inn_mask], ['СНИЛС', petition.snils_mask], ['Счёт', petition.account_mask]]
    return build_document('award-petition', fmt, f'Ходатайство на премию · {petition.year}', ['Реквизит', 'Значение'], rows,
        'Политика: одно лучшее премируемое выступление за год. Срок подачи — до 1 февраля следующего года. Суммы демо-правил не являются официальными.')


@roles_required('federation', 'ministry')
def ministry(request):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return HttpResponseForbidden('Кабинет министерства доступен только для чтения.')
    year = integer(request.GET.get('year'), timezone.localdate().year, 1900, timezone.localdate().year)
    comps = Competition.objects.exclude(state='draft').filter(starts_on__year=year).prefetch_related('disciplines')
    return render(request, 'governance/ministry.html', {'year': year, 'counts': census(year - 1, year), 'competitions': comps,
        'teams': Team.objects.filter(season=year).select_related('discipline').prefetch_related('members__user'), 'organizations': Organization.objects.all()})
