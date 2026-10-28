"""Federation-only mail readiness and an explicit test to the current user."""
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .access import roles_required
from .models import Notification
from .services import audit, notify


TEST_SUBJECT = 'Проверка внешних уведомлений ОренКод'
DELIVERY_LABELS = {
    'queued': 'В очереди', 'processing': 'Передаётся почтовому серверу',
    'local': 'Сохранено локально',
    'sent': 'Принято почтовым сервером', 'failed': 'Ошибка передачи',
    'skipped': 'Не отправлено: адрес не указан',
}


@require_http_methods(['GET', 'POST'])
@roles_required('federation')
def mail_status(request):
    smtp_selected = settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    configured = smtp_selected and bool(settings.EMAIL_HOST)
    if request.method == 'POST':
        if not configured:
            messages.error(request, 'Внешняя почта не настроена. Сначала подключите почтовый сервис.')
        elif not request.user.email:
            messages.error(request, 'Укажите адрес электронной почты в своём профиле.')
        else:
            with transaction.atomic():
                item = notify(request.user, TEST_SUBJECT,
                    'Это проверка почтового канала платформы ОренКод. '
                    'Если письмо получено, уведомления передаются на адрес вашего профиля. '
                    'Тест не содержит персональных данных участников.', reverse('mail_status'))
                audit(request, 'Проверка почтовых уведомлений', f'notification:{item.pk}',
                      'Тест запрошен пользователем для собственного адреса.')
            messages.success(request, 'Тестовое уведомление создано. Проверьте состояние ниже и свой почтовый ящик.')
        return redirect('mail_status')
    counts = {row['email_delivery_status']: row['total'] for row in
              Notification.objects.values('email_delivery_status').annotate(total=Count('pk'))}
    states = [{'label': label, 'total': counts.get(value, 0)} for value, label in DELIVERY_LABELS.items()]
    own_tests = list(Notification.objects.filter(user=request.user, title=TEST_SUBJECT).order_by('-created_at')[:5])
    for item in own_tests:
        item.delivery_label = DELIVERY_LABELS.get(item.email_delivery_status, item.email_delivery_status)
    return render(request, 'federation/mail_status.html', {
        'mail_configured': configured, 'states': states, 'own_tests': own_tests,
        'has_own_email': bool(request.user.email),
    })
