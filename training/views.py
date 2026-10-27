from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from federation.access import active_role, roles_required
from federation.models import CoachLink, Profile
from federation.services import audit, notify
from .forms import (AssessmentForm, AttendanceForm, EnrollmentForm, EnrollmentInfoForm, GroupForm, NormForm, ProgramForm, SessionForm, TransferForm)
from .models import (AssessmentNorm, AssessmentResult, Attendance, Attestation, Enrollment, OrganizationProgram, Session, TrainingGroup)
from .services import actor_can_manage_athlete, enroll, readiness, transfer_enrollment


def group_access(request, group, write=False):
    role = active_role(request)
    if role == 'federation':
        return
    if role == 'coach' and group.coach.user_id == request.user.pk:
        return
    if not write and role == 'athlete' and group.enrollments.filter(athlete__user=request.user).exists():
        return
    raise PermissionDenied('Группа доступна федерации, её тренеру и зачисленным спортсменам.')


def render_form(request, form, title, cancel='training:index', invalid=False):
    return render(request, 'federation/form.html', {'form': form, 'title': title, 'cancel_url': reverse(cancel)},
                  status=400 if invalid else 200)


@login_required
def index(request):
    role = active_role(request)
    groups = TrainingGroup.objects.select_related('organization', 'discipline', 'coach__user')
    if role == 'coach':
        groups = groups.filter(coach__user=request.user)
    elif role == 'athlete':
        groups = groups.filter(enrollments__athlete__user=request.user).distinct()
    elif role not in ('federation', 'ministry'):
        groups = groups.none()
    return render(request, 'training/index.html', {'groups': groups,
                  'programs': OrganizationProgram.objects.select_related('organization', 'discipline'),
                  'can_manage': role in ('federation', 'coach')})


@roles_required('federation')
def program_create(request, pk=None):
    program = get_object_or_404(OrganizationProgram, pk=pk) if pk else None
    form = ProgramForm(request.POST or None, instance=program)
    if request.method == 'POST' and form.is_valid():
        program = form.save()
        audit(request, 'Сохранена программа подготовки', f'training-program:{program.pk}', str(program))
        return redirect('training:index')
    return render_form(request, form, 'Программа спортивной подготовки', invalid=request.method == 'POST')


@roles_required('federation', 'coach')
def group_create(request, pk=None):
    group = get_object_or_404(TrainingGroup, pk=pk) if pk else None
    if group:
        group_access(request, group, write=True)
    form = GroupForm(request.POST or None, instance=group, user=request.user, role=active_role(request))
    if request.method == 'POST' and form.is_valid():
        group = form.save()
        audit(request, 'Сохранена учебно-тренировочная группа', f'training-group:{group.pk}', group.name)
        return redirect('training:group_detail', pk=group.pk)
    return render_form(request, form, 'Учебно-тренировочная группа', invalid=request.method == 'POST')


@login_required
def group_detail(request, pk):
    group = get_object_or_404(TrainingGroup.objects.select_related('coach__user', 'organization', 'discipline'), pk=pk)
    group_access(request, group)
    role, today = active_role(request), timezone.localdate()
    enrollments = group.enrollments.select_related('athlete__user')
    results = AssessmentResult.objects.filter(athlete__training_enrollments__group=group, norm__discipline=group.discipline,
                                             norm__stage=group.stage).select_related('athlete__user', 'norm').distinct()
    sessions = group.sessions.all()
    if role == 'athlete':
        enrollments = enrollments.filter(athlete__user=request.user)
        results = results.filter(athlete__user=request.user)
        own = list(enrollments)
        permitted_dates = Q(pk__in=[])
        for record in own:
            interval = Q(date__gte=record.start_date)
            if record.end_date:
                interval &= Q(date__lte=record.end_date)
            permitted_dates |= interval
        sessions = sessions.filter(permitted_dates)
    elif role == 'coach':
        linked = CoachLink.objects.filter(coach=group.coach, discipline=group.discipline, status='active').values('athlete_id')
        enrollments = enrollments.filter(athlete_id__in=linked)
        results = results.filter(athlete_id__in=linked)
    roster = [{'enrollment': record, 'readiness': readiness(record),
               'current': record.start_date <= today and (record.end_date is None or record.end_date >= today)} for record in enrollments]
    return render(request, 'training/group_detail.html', {'group': group, 'roster': roster, 'sessions': sessions,
                  'results': results[:100], 'can_manage': role in ('federation', 'coach'),
                  'attestations': Attestation.objects.filter(enrollment__in=enrollments).select_related('enrollment__athlete__user', 'destination')})


@roles_required('federation', 'coach')
def enrollment_create(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    group_access(request, group, write=True)
    form = EnrollmentForm(request.POST or None, group=group, user=request.user, role=active_role(request))
    if request.method == 'POST' and form.is_valid():
        try:
            record = enroll(group=group, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            audit(request, 'Зачислен спортсмен', f'enrollment:{record.pk}', str(record))
            notify(record.athlete.user, 'Зачисление в тренировочную группу', f'{group.name}, {group.get_stage_display()}', reverse('training:group_detail', args=[pk]))
            return redirect('training:group_detail', pk=pk)
    return render_form(request, form, 'Зачислить спортсмена', invalid=request.method == 'POST')


@roles_required('federation', 'coach')
def enrollment_info(request, pk):
    enrollment = get_object_or_404(Enrollment.objects.select_related('group__coach', 'athlete'), pk=pk)
    group_access(request, enrollment.group, write=True)
    if not actor_can_manage_athlete(request.user, active_role(request), enrollment.group, enrollment.athlete):
        raise PermissionDenied
    previous = f'{enrollment.municipality}; {enrollment.sex}'
    form = EnrollmentInfoForm(request.POST or None, instance=enrollment)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Уточнены данные зачисления', f'enrollment:{pk}', f'{previous} → {enrollment.municipality}; {enrollment.sex}')
        return redirect('training:group_detail', pk=enrollment.group_id)
    return render_form(request, form, 'Уточнить данные зачисления', invalid=request.method == 'POST')


@require_POST
@roles_required('federation', 'coach')
@transaction.atomic
def enrollment_end(request, pk):
    enrollment = get_object_or_404(Enrollment.objects.select_for_update().select_related('group__coach', 'athlete'), pk=pk)
    group_access(request, enrollment.group, write=True)
    if not actor_can_manage_athlete(request.user, active_role(request), enrollment.group, enrollment.athlete):
        raise PermissionDenied
    if enrollment.end_date:
        return HttpResponseBadRequest('Зачисление уже завершено.')
    today = timezone.localdate()
    if enrollment.start_date > today:
        return HttpResponseBadRequest('Будущее зачисление нельзя завершить текущей датой.')
    enrollment.end_date = today
    enrollment.full_clean()
    enrollment.save(update_fields=['end_date'])
    audit(request, 'Завершено зачисление', f'enrollment:{pk}', str(enrollment))
    notify(enrollment.athlete.user, 'Завершение занятий в группе', enrollment.group.name, reverse('training:index'))
    return redirect('training:group_detail', pk=enrollment.group_id)


@roles_required('federation', 'coach')
def session_create(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    group_access(request, group, write=True)
    form = SessionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        session = form.save(commit=False)
        session.group, session.created_by = group, request.user
        session.save()
        audit(request, 'Записано тренировочное занятие', f'training-session:{session.pk}', session.topic)
        return redirect('training:session_detail', pk=session.pk)
    return render_form(request, form, 'Занятие в журнале', invalid=request.method == 'POST')


@roles_required('federation', 'coach')
def session_edit(request, pk):
    session = get_object_or_404(Session.objects.select_related('group__coach'), pk=pk)
    group_access(request, session.group, write=True)
    form = SessionForm(request.POST or None, instance=session)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Уточнена запись журнала', f'training-session:{pk}', session.topic)
        return redirect('training:session_detail', pk=pk)
    return render_form(request, form, 'Редактировать занятие', invalid=request.method == 'POST')


@login_required
def session_detail(request, pk):
    session = get_object_or_404(Session.objects.select_related('group__coach', 'group__discipline'), pk=pk)
    group_access(request, session.group, write=request.method == 'POST')
    role = active_role(request)
    if role == 'athlete' and not session.group.enrollments.filter(athlete__user=request.user, start_date__lte=session.date).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=session.date)).exists():
        raise PermissionDenied('Вы не были зачислены в группу на дату занятия.')
    form = AttendanceForm(request.POST if request.method == 'POST' else None, session=session, user=request.user, role=role) if role in ('federation', 'coach') else None
    if request.method == 'POST' and form and form.is_valid():
        selected = set(form.cleaned_data['present'].values_list('pk', flat=True))
        with transaction.atomic():
            for person in form.fields['present'].queryset:
                attendance = Attendance.objects.filter(session=session, athlete=person).first() or Attendance(session=session, athlete=person)
                attendance.present = person.pk in selected
                attendance.full_clean()
                attendance.save()
            audit(request, 'Обновлена посещаемость', f'training-session:{pk}', f'Присутствовали: {len(selected)}')
        messages.success(request, 'Посещаемость сохранена.')
        return redirect('training:session_detail', pk=pk)
    attendance = session.attendance.select_related('athlete__user')
    if role == 'athlete':
        attendance = attendance.filter(athlete__user=request.user)
    elif role == 'coach':
        attendance = attendance.filter(athlete__coach_links__coach=session.group.coach, athlete__coach_links__discipline=session.group.discipline,
                                       athlete__coach_links__status='active').distinct()
    return render(request, 'training/session.html', {'session': session, 'form': form, 'attendance': attendance},
                  status=400 if request.method == 'POST' else 200)


@login_required
def norms(request):
    queryset = AssessmentNorm.objects.select_related('discipline')
    return render(request, 'training/norms.html', {'norms': queryset})


@roles_required('federation')
def norm_create(request, pk=None):
    norm = get_object_or_404(AssessmentNorm, pk=pk) if pk else None
    form = NormForm(request.POST or None, instance=norm)
    if request.method == 'POST' and form.is_valid():
        norm = form.save()
        audit(request, 'Сохранён норматив подготовки', f'training-norm:{norm.pk}', str(norm))
        return redirect('training:norms')
    return render_form(request, form, 'Норматив спортивной подготовки', invalid=request.method == 'POST')


@roles_required('federation', 'coach')
def assessment_create(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    group_access(request, group, write=True)
    form = AssessmentForm(request.POST or None, group=group, user=request.user, role=active_role(request))
    if request.method == 'POST' and form.is_valid():
        record = form.save(commit=False)
        record.recorded_by = request.user
        record.save()
        audit(request, 'Записана сдача норматива', f'assessment:{record.pk}', f'{record.athlete}: {record.value} {record.norm.unit}')
        notify(record.athlete.user, 'Результат сдачи норматива', f'{record.norm.name}: {record.value} {record.norm.unit}; {"выполнен" if record.passed else "не выполнен"}', reverse('training:group_detail', args=[pk]))
        return redirect('training:group_detail', pk=pk)
    return render_form(request, form, 'Результат сдачи норматива', invalid=request.method == 'POST')


@roles_required('federation', 'coach')
def transfer(request, pk):
    enrollment = get_object_or_404(Enrollment.objects.select_related('group__coach', 'athlete'), pk=pk)
    group_access(request, enrollment.group, write=True)
    if not actor_can_manage_athlete(request.user, active_role(request), enrollment.group, enrollment.athlete):
        raise PermissionDenied
    form = TransferForm(request.POST or None, enrollment=enrollment)
    if request.method == 'POST' and form.is_valid():
        try:
            record = transfer_enrollment(enrollment, form.cleaned_data['target_group'], form.cleaned_data['date'], request.user)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            audit(request, 'Проведена аттестация и перевод', f'attestation:{record.pk}', f'{enrollment.group} → {record.destination}')
            notify(enrollment.athlete.user, 'Перевод на следующий этап', record.destination.name, reverse('training:group_detail', args=[record.destination_id]))
            return redirect('training:group_detail', pk=record.destination_id)
    return render(request, 'training/transfer.html', {'form': form, 'enrollment': enrollment, 'readiness': readiness(enrollment)},
                  status=400 if request.method == 'POST' else 200)
