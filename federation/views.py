from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .access import active_role, can_view_person, managed_profiles, role_profiles, roles_required
from .documents import build_document
from .forms import (ApplicationForm, CoachLinkForm, CompetitionForm, FederationProfileForm,
                    JudgeAssignmentForm, OrganizationForm, ProfileForm, QualificationForm,
                    RegistrationForm, ResultForm, RANK_TITLES, configured_titles)
from .models import (Application, AuditLog, CoachLink, Competition, Consent, Discipline,
                     JudgeAssignment, Notification, Organization, Profile, RankApplication,
                     RatingRule, Result, Role, Team)
from .services import audit, calculate_rating, census, census_breakdown, integer, notify, layout_document
from .search import search_profiles, search_organizations


def form_page(request, form, title, cancel='dashboard', status=200):
    return render(request, 'federation/form.html', {'form': form, 'title': title,
                  'submit_label': 'Сохранить', 'cancel_url': reverse(cancel)}, status=status)


def competitions_for(request):
    qs = Competition.objects.prefetch_related('disciplines')
    return qs if active_role(request) == 'federation' else qs.exclude(state='draft')


def results_for(person):
    return Result.objects.filter(application__athlete=person, application__status='approved',
                                 application__competition__protocol_published=True).select_related(
                                     'application__competition', 'application__discipline', 'application__athlete__user')


def protocol_is_complete(competition):
    if competition.state not in ('closed', 'finished'):
        return False
    applications = competition.applications
    approved = applications.filter(status='approved')
    return (not applications.filter(status='pending').exists() and approved.exists()
            and not approved.filter(result__isnull=True).exists())


def dashboard_period(request, today):
    def value(name, fallback):
        try:
            return parse_date(request.GET.get(name, '')) or fallback
        except (TypeError, ValueError):
            return fallback
    start = value('date_from', date(today.year, 1, 1))
    end = value('date_to', today)
    return (start, end) if start <= end else (end, start)


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        request.session['active_role'] = user.profile.roles[0]
        audit(request, 'Регистрация', f'profile:{user.profile.pk}', 'Согласия сохранены')
        notify(user, 'Добро пожаловать в ОренКод', 'Ваш профиль создан. Выберите соревнование в календаре.', reverse('dashboard'))
        return redirect('dashboard')
    return render(request, 'federation/register.html', {'form': form}, status=400 if request.method == 'POST' else 200)


@require_POST
@login_required
def switch_role(request):
    role = request.POST.get('role')
    person = getattr(request.user, 'profile', None)
    if not person or role not in person.roles:
        raise PermissionDenied('Роль не назначена аккаунту.')
    request.session['active_role'] = role
    audit(request, 'Переключение роли', 'account', dict(Role.choices)[role])
    # Role-specific destination must remain accessible after the switch.
    return redirect('dashboard')


@login_required
def dashboard(request):
    role, today = active_role(request), timezone.localdate()
    if role == 'ministry':
        return redirect('governance:ministry')
    people = managed_profiles(request)
    visible = competitions_for(request)
    upcoming = visible.filter(starts_on__gte=today).order_by('starts_on')[:4]
    apps = Application.objects.all() if role == 'federation' else Application.objects.filter(athlete__in=people)
    ranks = RankApplication.objects.all() if role == 'federation' else RankApplication.objects.filter(athlete__in=people)
    results = Result.objects.filter(application__competition__protocol_published=True).select_related('application__athlete__user', 'application__competition', 'application__discipline')
    if role != 'federation':
        results = results.filter(application__athlete__in=people)
    stats = [
        {'label': 'Спортсменов в реестре' if role == 'federation' else 'Мои результаты', 'value': role_profiles('athlete').filter(status='active').count() if role == 'federation' else results.count(), 'detail': 'Только актуальные записи'},
        {'label': 'Соревнований в сезоне', 'value': visible.filter(starts_on__year=today.year).count(), 'detail': f'Сезон {today.year}'},
        {'label': 'Заявок на рассмотрении', 'value': apps.filter(status='pending').count(), 'detail': 'Ожидают решения федерации'},
        {'label': 'Действующих разрядов', 'value': ranks.filter(kind='rank', status='approved', valid_until__gte=today).count(), 'detail': 'С подтверждённым основанием'},
    ]
    if role == 'judge':
        stats[0] = {'label': 'Мои назначения', 'value': JudgeAssignment.objects.filter(judge__user=request.user).count(), 'detail': 'Вся история судейства'}
        category = request.user.profile.current_category
        stats[3] = {'label': 'Действующая категория', 'value': 'Есть' if category else 'Нет', 'detail': f'До {category.valid_until:%d.%m.%Y}' if category else 'Подайте заявку в разделе категорий'}
    period_start, period_end = dashboard_period(request, today)
    candidates = []
    tasks = []
    if role == 'federation':
        period_competitions = visible.filter(starts_on__range=(period_start, period_end))
        stats = [
            stats[0],
            {'label': 'Проведённых соревнований', 'value': period_competitions.filter(state='finished').count(), 'detail': 'За выбранный период'},
            {'label': 'Запланированных соревнований', 'value': period_competitions.exclude(state='finished').count(), 'detail': 'За выбранный период'},
            {'label': 'Участников соревнований', 'value': apps.filter(status='approved', competition__starts_on__range=(period_start, period_end)).values('athlete_id').distinct().count(), 'detail': 'Уникальные допущенные спортсмены за период'},
            {'label': 'Присвоенных разрядов', 'value': ranks.filter(kind='rank', status='approved', assigned_on__range=(period_start, period_end)).count(), 'detail': 'За выбранный период'},
            {'label': 'Действующих судейских категорий', 'value': ranks.filter(kind='category', status='approved', assigned_on__lte=today, valid_until__gte=today).values('athlete_id').distinct().count(), 'detail': 'Судей с действующей категорией на сегодня'},
            stats[2], stats[3],
        ]
        pending = ranks.filter(status='pending').count()
        tasks.append({'title': f'{apps.filter(status="pending").count()} заявок на соревнования', 'detail': 'Проверьте допуск участников', 'url': reverse('competitions')})
        tasks.append({'title': f'{pending} заявок на разряды и категории', 'detail': 'Рассмотрите документы и основание', 'url': reverse('qualifications')})
        from development.services import rank_candidates
        candidates = rank_candidates()
        tasks.append({'title': f'{len({item["athlete"].pk for item in candidates})} кандидатов на разряд', 'detail': 'Проверьте основания и настроенные критерии', 'url': reverse('development:candidates')})
        award_due = date(today.year if today < date(today.year, 2, 1) else today.year + 1, 2, 1)
        tasks.append({'title': 'Ходатайства на премии Губернатора', 'detail': f'Подготовить до {award_due:%d.%m.%Y}', 'url': reverse('governance:awards')})
    from .deadlines import dashboard_deadline_tasks
    tasks.extend(dashboard_deadline_tasks(request.user, today, role=role))
    for rank in ranks.filter(status='approved', valid_until__range=(today, today + timedelta(days=30))).select_related('athlete__user')[:4]:
        tasks.append({'title': f'Истекает: {rank.requested_title}', 'detail': f'{rank.athlete.display_name} · до {rank.valid_until:%d.%m.%Y}', 'url': reverse('qualifications')})
    for comp in visible.filter(state='open', registration_closes__range=(today, today + timedelta(days=14)))[:3]:
        tasks.append({'title': 'Завершается приём заявок', 'detail': f'{comp.title} · до {comp.registration_closes:%d.%m}', 'url': reverse('competition_detail', args=[comp.pk])})
    disc_stats, total = [], max(1, role_profiles('athlete').filter(status='active').count())
    for disc in Discipline.objects.all():
        count = role_profiles('athlete').filter(disciplines=disc, status='active').count()
        disc_stats.append({'name': disc.name, 'count': count, 'percent': round(count / total * 100)})
    return render(request, 'federation/dashboard.html', {'stats': stats, 'upcoming': upcoming, 'tasks': tasks,
                  'recent_results': results.order_by('-application__competition__starts_on', 'place')[:5],
                  'discipline_stats': disc_stats, 'recent_activity': AuditLog.objects.select_related('user')[:5] if role == 'federation' else [], 'today': today,
                  'period_start': period_start, 'period_end': period_end, 'rank_candidates': candidates[:5],
                  'judge_assignments': JudgeAssignment.objects.filter(judge__user=request.user).select_related('competition') if role == 'judge' else [],
                  'current_category': request.user.profile.current_category if role == 'judge' else None})


@login_required
def competitions(request):
    qs = competitions_for(request)
    if request.GET.get('q'):
        qs = qs.filter(Q(title__icontains=request.GET['q']) | Q(location__icontains=request.GET['q']))
    if request.GET.get('discipline', '').isdigit():
        qs = qs.filter(disciplines=request.GET['discipline'])
    if request.GET.get('state') in dict(Competition.STATES):
        qs = qs.filter(state=request.GET['state'])
    if request.GET.get('age', '').isdigit():
        age = integer(request.GET['age'], 18, 0, 120)
        qs = qs.filter(age_min__lte=age, age_max__gte=age)
    if request.GET.get('plan') in ('regional', 'ekp'):
        qs = qs.filter(calendar_plan=request.GET['plan'])
    if request.GET.get('eligible') == '1' and active_role(request) == 'athlete':
        person = request.user.profile
        ids = [c.pk for c in qs if person.age_on(c.starts_on) is not None and c.age_min <= person.age_on(c.starts_on) <= c.age_max and c.disciplines.filter(pk__in=person.disciplines.all()).exists()]
        qs = qs.filter(pk__in=ids)
    return render(request, 'federation/competitions.html', {'competitions': qs.distinct(), 'disciplines': Discipline.objects.all()})


@roles_required('federation')
def competition_create(request):
    form = CompetitionForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        comp = form.save()
        audit(request, 'Создано соревнование', f'competition:{comp.pk}', comp.title)
        messages.success(request, 'Соревнование создано.')
        return redirect('competition_detail', pk=comp.pk)
    return form_page(request, form, 'Новое соревнование', 'competitions', 400 if request.method == 'POST' else 200)


@roles_required('federation')
@transaction.atomic
def competition_edit(request, pk):
    comp = get_object_or_404(Competition.objects.select_for_update(), pk=pk)
    if comp.protocol_published:
        raise PermissionDenied('Опубликованный протокол фиксирует данные соревнования.')
    form = CompetitionForm(request.POST or None, request.FILES or None, instance=comp)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Изменено соревнование', f'competition:{pk}', comp.title)
        return redirect('competition_detail', pk=pk)
    return form_page(request, form, 'Редактировать соревнование', 'competitions', 400 if request.method == 'POST' else 200)


@login_required
def competition_detail(request, pk):
    comp = get_object_or_404(competitions_for(request), pk=pk)
    role = active_role(request)
    apps = comp.applications.select_related('athlete__user', 'athlete__organization', 'discipline')
    if role != 'federation':
        apps = apps.filter(athlete__in=managed_profiles(request))
    results = Result.objects.filter(application__competition=comp, application__status='approved').select_related('application__athlete__user', 'application__discipline')
    if not comp.protocol_published and role != 'federation':
        results = results.none()
    can_apply = role in ('athlete', 'coach') and comp.is_registration_open
    return render(request, 'federation/competition_detail.html', {
        'competition': comp, 'applications': apps, 'results': results.order_by('application__discipline', 'place'),
        'judges': comp.judge_assignments.select_related('judge__user'), 'can_manage': role == 'federation', 'can_apply': can_apply,
        'can_preview_protocol': role == 'federation' and not comp.protocol_published and protocol_is_complete(comp),
        'application_form': ApplicationForm(user=request.user, competition=comp, role=role) if can_apply else None,
        'result_form': ResultForm(competition=comp) if role == 'federation' and comp.state == 'closed' else None,
        'judge_form': JudgeAssignmentForm(competition=comp) if role == 'federation' and not comp.protocol_published else None})


@require_POST
@roles_required('federation')
@transaction.atomic
def competition_action(request, pk):
    comp = get_object_or_404(Competition.objects.select_for_update(), pk=pk)
    action = request.POST.get('action')
    error = None
    if action in ('publish_competition', 'open', 'reopen', 'publish'):
        try:
            comp.validate_publication_details()
        except ValidationError as validation:
            error = ' '.join(validation.messages)
    if error:
        pass
    elif comp.protocol_published:
        error = 'Протокол уже опубликован; результаты зафиксированы.'
    elif action in ('open', 'reopen'):
        if comp.state not in ('draft', 'published', 'closed'):
            error = 'Регистрацию можно открыть из черновика или после закрытия.'
        elif not comp.regulation:
            error = 'Сначала загрузите положение соревнования.'
        elif comp.registration_closes < timezone.localdate():
            error = 'Срок регистрации истёк. Обновите даты соревнования.'
        else:
            comp.state = 'open'
    elif action == 'publish_competition' and comp.state == 'draft':
        if not comp.regulation:
            error = 'Для публикации загрузите положение.'
        else:
            comp.state = 'published'
    elif action == 'close' and comp.state == 'open':
        comp.state = 'closed'
    elif action == 'publish' and comp.state == 'closed':
        approved = comp.applications.filter(status='approved')
        if comp.applications.filter(status='pending').exists():
            error = 'Сначала рассмотрите все заявки.'
        elif not approved.exists() or approved.filter(result__isnull=True).exists():
            error = 'Введите результаты всех одобренных участников.'
        else:
            comp.protocol_published, comp.state = True, 'finished'
    else:
        error = 'Недопустимый переход состояния соревнования.'
    if error:
        messages.error(request, error)
    else:
        comp.save()
        audit(request, f'Соревнование: {action}', f'competition:{pk}', comp.title)
        if action == 'publish':
            for app in comp.applications.filter(status='approved').select_related('athlete__user'):
                notify(app.athlete.user, 'Протокол опубликован', comp.title, reverse('competition_detail', args=[pk]))
        elif action in ('open', 'reopen'):
            for person in role_profiles('athlete').filter(disciplines__in=comp.disciplines.all(), status='active').distinct():
                notify(person.user, 'Открыта регистрация', comp.title, reverse('competition_detail', args=[pk]))
        messages.success(request, 'Состояние соревнования обновлено.')
    response = redirect('competition_detail', pk=pk)
    if error:
        response['X-Action-Error'] = 'invalid_transition'
    return response


@require_POST
@roles_required('athlete', 'coach')
@transaction.atomic
def application_create(request, pk):
    comp = get_object_or_404(competitions_for(request).select_for_update(), pk=pk)
    form = ApplicationForm(request.POST, user=request.user, competition=comp, role=active_role(request))
    if form.is_valid():
        app = form.save(commit=False)
        app.competition, app.submitted_by = comp, request.user
        try:
            with transaction.atomic():
                app.full_clean()
                app.save()
        except (ValidationError, IntegrityError):
            form.add_error(None, 'Заявка уже существует или не соответствует условиям соревнования.')
        else:
            audit(request, 'Подана заявка', f'application:{app.pk}', comp.title)
            notify(request.user, 'Заявка отправлена', f'{app.athlete}: {comp.title}', reverse('competition_detail', args=[pk]))
            messages.success(request, 'Заявка отправлена на рассмотрение.')
            return redirect('competition_detail', pk=pk)
    return form_page(request, form, 'Заявка на соревнование', 'competitions', 400)


@require_POST
@roles_required('federation')
@transaction.atomic
def application_moderate(request, pk):
    app = get_object_or_404(Application.objects.select_for_update().select_related('competition', 'athlete__user'), pk=pk)
    status, comment = request.POST.get('status'), request.POST.get('comment', '').strip()
    if app.competition.protocol_published or hasattr(app, 'result') or app.status != 'pending':
        return HttpResponseBadRequest('Эта заявка уже рассмотрена или имеет результат.')
    if status not in ('approved', 'rejected') or (status == 'rejected' and not comment):
        return HttpResponseBadRequest('При отклонении укажите причину.')
    app.status, app.comment = status, comment
    app.save()
    audit(request, 'Рассмотрена заявка', f'application:{pk}', f'{status}: {comment}')
    notify(app.athlete.user, 'Статус заявки изменён', f'{app.competition}: {app.get_status_display()}. {comment}', reverse('competition_detail', args=[app.competition_id]))
    return redirect('competition_detail', pk=app.competition_id)


@require_POST
@roles_required('federation')
@transaction.atomic
def result_save(request, pk):
    comp = get_object_or_404(Competition.objects.select_for_update(), pk=pk)
    if comp.protocol_published or comp.state != 'closed':
        return HttpResponseBadRequest('Результаты вносятся только после закрытия заявок и до публикации протокола.')
    form = ResultForm(request.POST, competition=comp)
    if form.is_valid():
        result = form.save()
        audit(request, 'Внесён результат', f'result:{result.pk}', f'{result.application.athlete}: {result.place} место, {result.score}')
        return redirect('competition_detail', pk=pk)
    return form_page(request, form, 'Результат соревнования', 'competitions', 400)


@require_POST
@roles_required('federation')
@transaction.atomic
def judge_assign(request, pk):
    comp = get_object_or_404(Competition.objects.select_for_update(), pk=pk)
    if comp.protocol_published:
        raise PermissionDenied
    form = JudgeAssignmentForm(request.POST, competition=comp)
    if form.is_valid():
        assignment = form.save()
        audit(request, 'Назначен судья', f'competition:{pk}', str(assignment))
        notify(assignment.judge.user, 'Судейское назначение', f'{comp.title}: {assignment.position}', reverse('competition_detail', args=[pk]))
        return redirect('competition_detail', pk=pk)
    return form_page(request, form, 'Назначение судьи', 'competitions', 400)


@login_required
def regulation_download(request, pk):
    comp = get_object_or_404(competitions_for(request), pk=pk)
    if not comp.regulation:
        raise Http404
    try:
        return FileResponse(comp.regulation.open('rb'), as_attachment=True, filename=comp.regulation.name.rsplit('/', 1)[-1])
    except FileNotFoundError:
        raise Http404('Положение отсутствует в хранилище.')


@login_required
def registry(request):
    kind = request.GET.get('kind', 'athlete')
    if kind not in ('athlete', 'coach', 'judge', 'organization'):
        kind = 'athlete'
    qs = role_profiles(kind).select_related('user', 'organization') if kind != 'organization' else Profile.objects.none()
    organizations = Organization.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        qs = search_profiles(qs, q)
        organizations = search_organizations(organizations, q)
    if request.GET.get('discipline', '').isdigit():
        qs = qs.filter(disciplines=request.GET['discipline'])
    if request.GET.get('org', '').isdigit():
        qs = qs.filter(organization=request.GET['org'])
    if request.GET.get('status') in ('active', 'archived'):
        qs = qs.filter(status=request.GET['status'])
    if request.GET.get('municipality'):
        qs = qs.filter(municipality__icontains=request.GET['municipality'])
    if request.GET.get('rank'):
        qs = qs.filter(rank_applications__kind='category' if kind == 'judge' else 'rank', rank_applications__status='approved', rank_applications__valid_until__gte=timezone.localdate(), rank_applications__requested_title=request.GET['rank'])
    ids = []
    for p in qs:
        if request.GET.get('age_min', '').isdigit() and (p.age is None or p.age < int(request.GET['age_min'])):
            continue
        if request.GET.get('age_max', '').isdigit() and (p.age is None or p.age > int(request.GET['age_max'])):
            continue
        ids.append(p.pk)
    qs = qs.filter(pk__in=ids).distinct()
    return render(request, 'federation/registry.html', {'profiles': qs.prefetch_related('disciplines'),
                  'disciplines': Discipline.objects.all(), 'organizations': organizations, 'kind': kind})


@login_required
def athlete_detail(request, pk):
    person = get_object_or_404(Profile.objects.select_related('user', 'organization'), pk=pk)
    if not can_view_person(request, person):
        raise PermissionDenied('Личная карточка доступна спортсмену, его тренеру и федерации.')
    from training.services import athlete_training_history
    return render(request, 'federation/athlete_detail.html', {'person': person, 'results': results_for(person),
                  'training_history': athlete_training_history(person),
                  'ranks': person.rank_applications.filter(status='approved'),
                  'consents': person.user.consents.all() if person.user_id == request.user.pk or active_role(request) == 'federation' else [],
                  'links': person.coach_links.select_related('coach__user', 'discipline')})


@login_required
def profile(request):
    person = get_object_or_404(Profile, user=request.user)
    form = ProfileForm(request.POST or None, instance=person)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Изменён профиль', f'profile:{person.pk}')
        messages.success(request, 'Профиль сохранён.')
        return redirect('profile')
    from training.services import athlete_training_history
    return render(request, 'federation/profile.html', {'form': form, 'person': person, 'consents': request.user.consents.all(), 'results': results_for(person),
                  'training_history': athlete_training_history(person)}, status=400 if request.method == 'POST' else 200)


@roles_required('federation')
def profile_create(request):
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        audit(request, 'Создан профиль', f'profile:{user.profile.pk}')
        return redirect('athlete_detail', pk=user.profile.pk)
    return form_page(request, form, 'Добавить участника', 'registry', 400 if request.method == 'POST' else 200)


@roles_required('federation')
def profile_edit(request, pk):
    person = get_object_or_404(Profile, pk=pk)
    form = FederationProfileForm(request.POST or None, instance=person)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Федерация изменила профиль', f'profile:{pk}')
        return redirect('athlete_detail', pk=pk)
    return form_page(request, form, 'Управление профилем', 'registry', 400 if request.method == 'POST' else 200)


@roles_required('federation')
def organization_create(request):
    form = OrganizationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        org = form.save()
        audit(request, 'Создана организация', f'organization:{org.pk}', org.name)
        return redirect('registry')
    return form_page(request, form, 'Новая организация', 'registry', 400 if request.method == 'POST' else 200)


@login_required
def organization_detail(request, pk):
    organization = get_object_or_404(Organization, pk=pk)
    from training.models import OrganizationProgram, TrainingGroup
    # Registry details are shared; personal histories and internal audit stay scoped.
    people = Profile.objects.filter(organization=organization).select_related('user').prefetch_related('disciplines')
    events = AuditLog.objects.filter(entity=f'organization:{organization.pk}').select_related('user') if active_role(request) == 'federation' else []
    return render(request, 'federation/organization_detail.html', {
        'organization': organization, 'people': people,
        'programs': OrganizationProgram.objects.filter(organization=organization).select_related('discipline'),
        'groups': TrainingGroup.objects.filter(organization=organization).select_related('discipline', 'coach__user'),
        'organization_events': events,
    })


@roles_required('federation', 'athlete', 'coach', 'judge')
def qualifications(request):
    role = active_role(request)
    form = QualificationForm(request.POST or None, user=request.user, role=role)
    if request.method == 'POST' and form.is_valid():
        record = RankApplication(**form.cleaned_data, submitted_by=request.user)
        record.full_clean()
        record.save()
        audit(request, 'Запрошен разряд / категория', f'qualification:{record.pk}', record.requested_title)
        notify(record.athlete.user, 'Заявка на квалификацию отправлена', record.requested_title, reverse('qualifications'))
        for person in role_profiles('federation'):
            notify(person.user, 'Новая заявка на квалификацию', f'{record.athlete}: {record.requested_title}', reverse('qualifications'))
        return redirect('qualifications')
    records = RankApplication.objects.select_related('athlete__user', 'result__application__competition')
    if role != 'federation':
        records = records.filter(athlete__in=managed_profiles(request))
        if role == 'judge':
            records = records.filter(kind='category')
    return render(request, 'federation/qualifications.html', {'records': records, 'form': form, 'is_federation': role == 'federation'}, status=400 if request.method == 'POST' else 200)


@require_POST
@roles_required('federation')
@transaction.atomic
def qualification_review(request, pk):
    record = get_object_or_404(RankApplication.objects.select_for_update().select_related('athlete__user'), pk=pk)
    if record.status != 'pending':
        return HttpResponseBadRequest('Заявка уже рассмотрена.')
    status = request.POST.get('status')
    comment = request.POST.get('review_comment', '').strip()
    if status not in ('approved', 'rejected') or (status == 'rejected' and not comment):
        return HttpResponseBadRequest('Укажите решение и причину отклонения.')
    record.status, record.review_comment = status, comment
    if status == 'approved':
        if record.kind == 'rank':
            from governance.services import valid_results
            if not valid_results(Result.objects.filter(pk=record.result_id), exclude_active_disqualified=True).exists():
                return HttpResponseBadRequest('Результат или допуск спортсмена утратил действительность. Проверьте решения о нарушениях.')
        try:
            record.assigned_on = parse_date(request.POST.get('assigned_on', ''))
            record.valid_until = parse_date(request.POST.get('valid_until', ''))
        except ValueError:
            return HttpResponseBadRequest('Проверьте даты присвоения и действия.')
        record.order_number = request.POST.get('order_number', '').strip()
        if record.assigned_on and record.assigned_on > timezone.localdate():
            return HttpResponseBadRequest('Дата присвоения не может быть в будущем.')
    try:
        record.full_clean()
    except ValidationError as error:
        return HttpResponseBadRequest(' '.join(error.messages))
    record.save()
    audit(request, 'Рассмотрена квалификация', f'qualification:{pk}', f'{status}: {record.requested_title}, приказ {record.order_number}')
    notify(record.athlete.user, 'Решение по квалификации', f'{record.requested_title}: {record.get_status_display()}. {comment}', reverse('qualifications'))
    return redirect('qualifications')


@roles_required('coach', 'athlete', 'federation')
def coaching(request):
    role = active_role(request)
    form = CoachLinkForm(request.POST or None, coach=request.user.profile) if role == 'coach' else None
    if request.method == 'POST':
        if role != 'coach':
            raise PermissionDenied
        if form.is_valid():
            link = form.save(commit=False)
            link.coach = request.user.profile
            try:
                link.full_clean()
                link.save()
            except ValidationError as error:
                form.add_error(None, error)
            else:
                audit(request, 'Запрос привязки', f'coachlink:{link.pk}', str(link))
                notify(link.athlete.user, 'Тренер приглашает к сотрудничеству', f'{link.coach}: {link.discipline}', reverse('coaching'))
                return redirect('coaching')
    links = CoachLink.objects.select_related('coach__user', 'athlete__user', 'discipline')
    if role == 'coach':
        links = links.filter(coach__user=request.user)
    elif role == 'athlete':
        links = links.filter(athlete__user=request.user)
    pending = links.filter(status='pending')
    rows = list(links)
    histories = {}
    for event in AuditLog.objects.filter(entity__in=[f'coachlink:{link.pk}' for link in rows]).select_related('user').order_by('created_at', 'pk'):
        histories.setdefault(event.entity, []).append(event)
    for link in rows:
        link.history_events = histories.get(f'coachlink:{link.pk}', [])
    return render(request, 'federation/coaching.html', {'links': rows, 'pending': pending, 'form': form}, status=400 if request.method == 'POST' else 200)


@require_POST
@roles_required('athlete', 'coach', 'federation')
def link_update(request, pk):
    link = get_object_or_404(CoachLink, pk=pk)
    role, status = active_role(request), request.POST.get('status')
    is_athlete = role == 'athlete' and link.athlete.user_id == request.user.pk
    is_coach = role == 'coach' and link.coach.user_id == request.user.pk
    if role != 'federation' and not (is_athlete or is_coach):
        raise PermissionDenied
    # Only athlete consent can activate a pending relationship.
    if status not in ('active', 'suspended', 'finished') or (status == 'active' and (not is_athlete or link.status not in ('pending', 'suspended'))):
        return HttpResponseBadRequest('Активацию подтверждает только спортсмен.')
    if link.status == 'finished':
        return HttpResponseBadRequest('Завершённую связь нельзя восстановить без нового согласия.')
    previous = link.status
    link.status = status
    link.save()
    audit(request, 'Изменена связь тренера', f'coachlink:{pk}', f'{previous} → {status}')
    for user in (link.coach.user, link.athlete.user):
        notify(user, 'Связь с тренером обновлена', f'{link.discipline}: {link.get_status_display()}', reverse('coaching'))
    return redirect('coaching')


def rating_filters(request, source=None):
    source = source if source is not None else request.GET
    season = integer(source.get('season'), timezone.localdate().year, 1900, 2100)
    discipline = integer(source.get('discipline'), Discipline.objects.values_list('pk', flat=True).first() or 0, 0, 1000000)
    age_min = integer(source.get('age_min'), 0, 0, 120)
    age_max = integer(source.get('age_max'), 99, 0, 120)
    return season, discipline, age_min, age_max


@login_required
def rating(request):
    season, discipline, age_min, age_max = rating_filters(request)
    entries = calculate_rating(season, discipline, age_min, age_max, request.GET.get('min_rank', ''))
    return render(request, 'federation/rating.html', {'entries': entries, 'disciplines': Discipline.objects.all(),
                  'season': season, 'selected_discipline': discipline, 'federated': active_role(request) == 'federation',
                  'team': Team.objects.filter(season=season, discipline_id=discipline).order_by('-created_at').first(),
                  'rules': RatingRule.objects.all(), 'age_min': age_min, 'age_max': age_max,
                  'rank_titles': configured_titles('rank', RANK_TITLES)})


@require_POST
@roles_required('federation')
@transaction.atomic
def team_create(request):
    season, discipline, age_min, age_max = rating_filters(request, request.POST)
    disc = get_object_or_404(Discipline, pk=discipline)
    ids = request.POST.getlist('members')
    entries = calculate_rating(season, discipline, age_min, age_max, request.POST.get('min_rank', ''))
    allowed = {str(e['athlete'].pk) for e in entries}
    if not ids or not set(ids).issubset(allowed) or age_min > age_max:
        return HttpResponseBadRequest('Выберите спортсменов из рейтинга указанной дисциплины и сезона.')
    team = Team.objects.create(season=season, discipline=disc, age_min=age_min, age_max=age_max, created_by=request.user)
    team.members.set(ids)
    audit(request, 'Сформирована сборная', f'team:{team.pk}', f'{season}, {disc.name}, {len(set(ids))} участников')
    messages.success(request, 'Состав сохранён. Документы доступны для скачивания.')
    response = redirect(f'{reverse("rating")}?season={season}&discipline={discipline}')
    response['X-Created-Object'] = str(team.pk)
    return response


@require_POST
@roles_required('federation')
@transaction.atomic
def rating_rules(request):
    values = []
    for rule in RatingRule.objects.all():
        try:
            points = [int(request.POST[f'{field}_{rule.pk}']) for field in ('first', 'second', 'third')]
        except (KeyError, ValueError):
            return HttpResponseBadRequest('Укажите целые баллы для каждого уровня.')
        if not 100000 >= points[0] >= points[1] >= points[2] >= 0:
            return HttpResponseBadRequest('Баллы должны убывать от первого к третьему месту.')
        values.append((rule, points))
    for rule, points in values:
        rule.first, rule.second, rule.third = points
        rule.save()
    audit(request, 'Изменены правила рейтинга', 'rating')
    return redirect('rating')


def census_filters(request):
    today = timezone.localdate()
    start = integer(request.GET.get('year_from'), today.year - 1, 1900, today.year)
    end = integer(request.GET.get('year_to'), today.year, start, today.year)
    start = max(start, end - 20)
    discipline = integer(request.GET.get('discipline'), 0, 0, 1000000) or None
    org = integer(request.GET.get('org'), 0, 0, 1000000) or None
    return start, end, discipline, org


def census_extra(request):
    return {'age_min': integer(request.GET.get('age_min'), None, 0, 120),
            'age_max': integer(request.GET.get('age_max'), None, 0, 120),
            'municipality': request.GET.get('municipality', '').strip() or None}


@roles_required('federation', 'ministry')
def reports(request):
    start, end, disc, org = census_filters(request)
    extra = census_extra(request)
    counts = census(start, end, disc, org, **extra)
    return render(request, 'federation/reports.html', {'counts': counts, 'disciplines': Discipline.objects.all(),
                  'organizations': Organization.objects.all(), 'year_from': start, 'year_to': end,
                  'breakdown': census_breakdown(start, end, disc, org, **extra),
                  'stats': {'max_count': max([x['total'] for x in counts] + [1])}})


@login_required
def export_document(request, kind, pk, fmt):
    role = active_role(request)
    if kind == 'protocol':
        comp = get_object_or_404(competitions_for(request), pk=pk)
        preliminary = not comp.protocol_published
        if preliminary:
            if role != 'federation':
                raise PermissionDenied('Предварительный протокол доступен только федерации.')
            if not protocol_is_complete(comp):
                return HttpResponseBadRequest('Закройте регистрацию, рассмотрите все заявки и внесите результаты всех допущенных участников.')
        results = Result.objects.filter(application__competition=comp, application__status='approved').select_related('application__athlete__user', 'application__athlete__organization', 'application__discipline').order_by('application__discipline', 'place')
        title, headers = f'{"Предварительный протокол" if preliminary else "Протокол"} · {comp.title}', ['Место', 'Спортсмен', 'Дисциплина', 'Организация', 'Баллы']
        rows = [[r.place, r.application.athlete.display_name, r.application.discipline.name,
                 str(r.application.athlete.organization or 'Без организации'), r.score] for r in results]
        subtitle = f'{comp.starts_on:%d.%m.%Y} · {comp.location} · {comp.get_level_display()}'
        if preliminary:
            subtitle += '\nПредварительный документ. Протокол не опубликован.'
    elif kind == 'team':
        if role not in ('federation', 'ministry'):
            raise PermissionDenied
        team = get_object_or_404(Team, pk=pk)
        title, headers = f'Сборная региона · {team.season}', ['№', 'Спортсмен', 'Организация', 'Дисциплина']
        rows = [[i, p.display_name, str(p.organization or 'Без организации'), team.discipline.name]
                for i, p in enumerate(team.members.select_related('user', 'organization'), 1)]
        subtitle = f'Проект приказа: утвердить следующий состав сборной. {team.age_min}–{team.age_max} лет. Подлежит проверке и подписанию федерацией.'
    elif kind == 'athlete':
        person = get_object_or_404(Profile, pk=pk)
        if not can_view_person(request, person):
            raise PermissionDenied
        title, headers = f'История выступлений · {person.display_name}', ['Дата', 'Соревнование', 'Дисциплина', 'Место', 'Баллы']
        rows = [[r.application.competition.starts_on, r.application.competition.title, r.application.discipline.name, r.place, r.score] for r in results_for(person)]
        subtitle = 'Источник: опубликованные протоколы соревнований платформы.'
    elif kind == 'census':
        if role not in ('federation', 'ministry'):
            raise PermissionDenied
        start, end, disc, org = census_filters(request)
        title, headers = 'Численность занимающихся спортивным программированием в Оренбургской области', ['Год', 'Разрез', 'Группа', 'Число спортсменов']
        extra = census_extra(request)
        rows = [[r['year'], 'Всего', 'Регион', r['total']] for r in census(start, end, disc, org, **extra)]
        rows.extend([[r['year'], r['dimension'], r['label'], r['total']] for r in census_breakdown(start, end, disc, org, **extra)])
        subtitle = 'Источник: история зачислений в группы; для периодов до начала истории — интервалы занятий реестра. Возраст на 31 декабря отчётного года. В итогах спортсмен учитывается один раз; суммы разрезов могут пересекаться.'
    elif kind == 'consent':
        consent = get_object_or_404(Consent, pk=pk)
        if consent.user_id != request.user.pk and role != 'federation':
            raise PermissionDenied
        title, headers = 'Подтверждение согласия на обработку данных', ['Поле', 'Значение']
        rows = [['Пользователь', consent.user.get_full_name() or consent.user.username], ['Вид', consent.get_kind_display()], ['Версия', consent.version], ['Дата', str(consent.accepted_at)], ['Представитель', consent.representative_name]]
        subtitle = 'Демонстрационная запись о согласии, не юридический шаблон.'
    else:
        raise Http404
    title, subtitle = layout_document(kind, title, subtitle)
    return build_document(kind, fmt, title, headers, rows, subtitle)


@login_required
def notifications(request):
    notices = Notification.objects.filter(user=request.user)
    if request.method == 'POST':
        if request.POST.get('action') == 'read_all':
            notices.update(read=True)
        else:
            notice = get_object_or_404(notices, pk=integer(request.POST.get('id'), 0))
            notice.read = True
            notice.save(update_fields=['read'])
        return redirect('notifications')
    return render(request, 'federation/notifications.html', {'notifications': notices})


@roles_required('federation')
def audit_log(request):
    return render(request, 'federation/audit.html', {'events': AuditLog.objects.select_related('user')[:200]})


def guide(request):
    return render(request, 'federation/guide.html', {'disciplines': Discipline.objects.all(),
        'public_parent': 'federation/base.html' if request.user.is_authenticated else 'federation/public_base.html',
        'organizations': Organization.objects.all(),
        'upcoming': Competition.objects.exclude(state='draft').filter(starts_on__gte=timezone.localdate())[:6]})


def health(request):
    # Querying the database catches missing migrations, not merely a live Python process.
    Discipline.objects.exists()
    return JsonResponse({'status': 'ok'})


@login_required
def api_competitions(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Метод не поддерживается', 'code': 'method_not_allowed'}, status=405)
    page = integer(request.GET.get('page'), 1, 1, 100000)
    qs = competitions_for(request)
    return JsonResponse({'count': qs.count(), 'page': page, 'results': list(qs.values(
        'id', 'title', 'starts_on', 'ends_on', 'state', 'level', 'protocol_published')[(page - 1) * 25:page * 25])})
