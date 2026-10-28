from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from federation.access import active_role, roles_required
from federation.services import audit

from .forms import PrivacyRequestForm, ReviewForm
from .models import PrivacyRequest
from .services import OPEN_STATUSES, create_request, personal_data, review_request


def visible_requests(request):
    queryset = PrivacyRequest.objects.select_related('user', 'reviewed_by')
    return queryset if active_role(request) == 'federation' else queryset.filter(user=request.user)


@never_cache
@login_required
def index(request):
    form = PrivacyRequestForm(request.POST or None)
    current = PrivacyRequest.objects.filter(user=request.user, status__in=OPEN_STATUSES).first()
    if request.method == 'POST' and form.is_valid():
        record, created = create_request(request, form.cleaned_data['reason'])
        messages.success(request, 'Обращение направлено в федерацию.' if created else 'У вас уже есть открытое обращение. Его состояние показано ниже.')
        return redirect('privacy_requests:detail', pk=record.pk)
    return render(request, 'privacy_requests/index.html', {
        'form': form, 'current': current,
        'my_requests': PrivacyRequest.objects.filter(user=request.user),
        'all_requests': visible_requests(request) if active_role(request) == 'federation' else None,
    }, status=400 if request.method == 'POST' else 200)


@never_cache
@login_required
def detail(request, pk):
    record = get_object_or_404(visible_requests(request), pk=pk)
    form = ReviewForm(record=record) if active_role(request) == 'federation' and record.can_review else None
    return render(request, 'privacy_requests/detail.html', {
        'record': record, 'events': record.events.select_related('actor'), 'review_form': form,
    })


@require_POST
@roles_required('federation')
@never_cache
def review(request, pk):
    record = get_object_or_404(PrivacyRequest, pk=pk)
    form = ReviewForm(request.POST, record=record)
    if form.is_valid():
        try:
            review_request(request, record.pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Ответ сохранён и доступен заявителю.')
            return redirect('privacy_requests:detail', pk=pk)
    return render(request, 'privacy_requests/detail.html', {
        'record': record, 'events': record.events.select_related('actor'), 'review_form': form,
    }, status=400)


@never_cache
@login_required
def export(request):
    # The subject is always the authenticated account: neither a form nor a URL
    # parameter can select another person.
    data = personal_data(request.user)
    audit(request, 'Выгружены собственные персональные данные', f'account:{request.user.pk}')
    response = JsonResponse(data, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="personal-data-{timezone.localdate():%Y%m%d}.json"'
    return response
