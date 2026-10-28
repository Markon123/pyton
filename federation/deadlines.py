"""Role-scoped report deadlines and durable reminder idempotency."""
import hashlib
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import active_role, role_profiles, roles_required
from .models import Notification, ReminderDispatch, ReportDeadline, Role
from .services import audit, notify


class ReportDeadlineForm(forms.ModelForm):
    class Meta:
        model = ReportDeadline
        fields = ['title', 'description', 'due_on', 'remind_days', 'recipients']
        widgets = {
            'due_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'recipients': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recipients'].queryset = get_user_model().objects.filter(
            is_active=True, profile__status='active').select_related('profile').order_by('last_name', 'first_name', 'username')
        self.fields['remind_days'].min_value = 0
        self.fields['remind_days'].max_value = 365


def visible_deadlines(user, role):
    roles = getattr(getattr(user, 'profile', None), 'roles', [])
    if role not in roles:
        return ReportDeadline.objects.none()
    queryset = ReportDeadline.objects.all()
    return queryset if role == Role.FEDERATION else queryset.filter(recipients=user).distinct()


@login_required
def deadline_list(request):
    role = active_role(request)
    can_manage = role == Role.FEDERATION
    queryset = visible_deadlines(request.user, role)
    editing = request.GET.get('edit')
    if editing and not can_manage:
        raise PermissionDenied('Изменять сроки отчётов может только федерация.')
    record = get_object_or_404(queryset, pk=editing) if editing and editing.isdigit() else None
    form = ReportDeadlineForm(request.POST or None, instance=record) if can_manage else None
    if request.method == 'POST':
        if not can_manage:
            raise PermissionDenied('Изменять сроки отчётов может только федерация.')
        if form.is_valid():
            with transaction.atomic():
                deadline = form.save(commit=False)
                if not deadline.pk:
                    deadline.created_by = request.user
                deadline.save()
                form.save_m2m()
                audit(request, 'Изменён срок отчёта' if record else 'Создан срок отчёта',
                      f'report_deadline:{deadline.pk}', f'{deadline.title} · до {deadline.due_on:%d.%m.%Y}')
            messages.success(request, 'Срок отчёта сохранён.')
            return redirect('report_deadlines')
    state = request.GET.get('state', 'active')
    if state == 'completed':
        queryset = queryset.filter(active=True, completed_at__isnull=False)
    elif state == 'archived':
        queryset = queryset.filter(active=False)
    elif state != 'all':
        queryset = queryset.filter(active=True, completed_at__isnull=True)
    return render(request, 'federation/report_deadlines.html', {
        'deadlines': queryset.select_related('created_by', 'completed_by').prefetch_related('recipients'),
        'deadline_form': form, 'editing_deadline': record, 'can_manage_deadlines': can_manage,
        'deadline_state': state, 'today': timezone.localdate(),
    }, status=400 if request.method == 'POST' else 200)


@require_POST
@roles_required(Role.FEDERATION)
@transaction.atomic
def deadline_complete(request, pk):
    deadline = get_object_or_404(ReportDeadline.objects.select_for_update(), pk=pk)
    action = request.POST.get('action', 'complete')
    changed = False
    if action == 'complete' and deadline.active and not deadline.completed_at:
        deadline.completed_at, deadline.completed_by = timezone.now(), request.user
        deadline.completion_note = request.POST.get('note', '').strip()[:4000]
        changed = True
    elif action == 'reopen' and deadline.active and deadline.completed_at:
        deadline.completed_at, deadline.completed_by, deadline.completion_note = None, None, ''
        changed = True
    elif action == 'archive' and deadline.active:
        deadline.active, changed = False, True
    elif action == 'restore' and not deadline.active:
        deadline.active, changed = True, True
    elif action not in ('complete', 'reopen', 'archive', 'restore'):
        messages.error(request, 'Неизвестное действие со сроком отчёта.')
        return redirect('report_deadlines')
    if changed:
        deadline.save()
        audit(request, 'Срок отчёта: ' + action, f'report_deadline:{deadline.pk}',
              f'{deadline.title} · {deadline.completion_note}')
        messages.success(request, 'Состояние срока отчёта обновлено.')
    return redirect('report_deadlines')


def dashboard_deadline_tasks(user, today, role=None):
    roles = getattr(getattr(user, 'profile', None), 'roles', [])
    selected = role if role is not None else (roles[0] if roles else None)
    tasks = []
    for deadline in visible_deadlines(user, selected).filter(active=True, completed_at__isnull=True):
        if deadline.due_on > today + timedelta(days=max(30, deadline.remind_days)):
            continue
        label = 'Просрочен отчёт' if deadline.due_on < today else 'Срок отчёта'
        tasks.append({'title': f'{label}: {deadline.title}',
                      'detail': f'До {deadline.due_on:%d.%m.%Y}', 'url': reverse('report_deadlines')})
    return tasks


@transaction.atomic
def dispatch_reminder_once(user, title, body, url):
    """Reserve one notification per user/title, including concurrent schedulers.

    A failed SMTP delivery does not release the reservation: the persistent
    notification outbox handles its retry, rather than producing another email.
    """
    key = hashlib.sha256(title.encode('utf-8')).hexdigest()
    delivery, created = ReminderDispatch.objects.get_or_create(user=user, key=key)
    if not created:
        return False
    existing = Notification.objects.filter(user=user, title=title).first()
    if existing:
        delivery.notification = existing
        delivery.save(update_fields=['notification'])
        return False
    delivery.notification = notify(user, title[:240], body, url)
    delivery.save(update_fields=['notification'])
    return True


def send_report_deadline_reminders(callback, today):
    federation = list(role_profiles(Role.FEDERATION).filter(status='active', user__is_active=True))
    deadlines = ReportDeadline.objects.filter(active=True, completed_at__isnull=True,
        due_on__gte=today, due_on__lte=today + timedelta(days=365)).prefetch_related(
            Prefetch('recipients', queryset=get_user_model().objects.select_related('profile')))
    for deadline in deadlines:
        if deadline.due_on > today + timedelta(days=deadline.remind_days):
            continue
        assigned = list(deadline.recipients.all())
        recipients = assigned if assigned else [person.user for person in federation]
        title = f'Отчёт № {deadline.pk} до {deadline.due_on:%d.%m.%Y} · {deadline.title}'
        body = deadline.description or 'Подготовьте и сдайте отчёт до указанного срока. После сдачи федерация отмечает выполнение.'
        for user in recipients:
            if user.is_active and getattr(getattr(user, 'profile', None), 'status', None) == 'active':
                callback(user, title, body, reverse('report_deadlines'))
