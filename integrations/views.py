import csv
from datetime import timedelta
import io
import json
import secrets

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from federation.access import roles_required
from federation.services import audit
from .forms import EndpointForm, ImportForm, TokenForm
from .models import PartnerToken, WebhookDelivery, WebhookEndpoint
from .services import encrypt_key, import_results


@roles_required('federation')
def index(request):
    token_form, endpoint_form, import_form = TokenForm(), EndpointForm(), ImportForm()
    raw_token = raw_key = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'token':
            token_form = TokenForm(request.POST)
            if token_form.is_valid():
                record, raw_token = PartnerToken.issue(name=token_form.cleaned_data['name'], scopes=token_form.cleaned_data['scopes'],
                    expires_at=timezone.now() + timedelta(days=token_form.cleaned_data['days']), created_by=request.user)
                audit(request, 'Выдан токен интеграции', f'partner:{record.pk}', record.name)
                token_form = TokenForm()
        elif action == 'endpoint':
            endpoint_form = EndpointForm(request.POST)
            if endpoint_form.is_valid():
                record = endpoint_form.save(commit=False)
                raw_key = secrets.token_urlsafe(32)
                record.encrypted_key = encrypt_key(raw_key)
                record.created_by = request.user
                record.save()
                audit(request, 'Добавлен webhook', f'webhook:{record.pk}', record.name)
                endpoint_form = EndpointForm()
        elif action == 'import':
            import_form = ImportForm(request.POST, request.FILES)
            if import_form.is_valid():
                uploaded = import_form.cleaned_data['file']
                try:
                    content = uploaded.read().decode('utf-8-sig')
                    rows = json.loads(content) if uploaded.name.lower().endswith('.json') else list(csv.DictReader(io.StringIO(content)))
                    if isinstance(rows, dict):
                        rows = rows.get('results')
                    imported = import_results(import_form.cleaned_data['competition'], rows, request.user)
                except (ValueError, UnicodeError, ValidationError) as error:
                    import_form.add_error(None, 'Ошибка импорта: ' + str(error))
                else:
                    messages.success(request, f'Импортировано результатов: {len(imported)}. Протокол ещё не опубликован.')
                    return redirect('integrations:index')
    response = render(request, 'integrations/index.html', {
        'token_form': token_form, 'endpoint_form': endpoint_form, 'import_form': import_form,
        'raw_token': raw_token, 'raw_key': raw_key, 'tokens': PartnerToken.objects.all(),
        'endpoints': WebhookEndpoint.objects.all(), 'deliveries': WebhookDelivery.objects.select_related('endpoint', 'event')[:40],
    })
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
@roles_required('federation')
def revoke_token(request, pk):
    token = get_object_or_404(PartnerToken, pk=pk)
    token.revoked = True
    token.save(update_fields=['revoked'])
    audit(request, 'Отозван токен интеграции', f'partner:{pk}')
    return redirect('integrations:index')


@require_POST
@roles_required('federation')
def disable_endpoint(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)
    endpoint.enabled = False
    endpoint.save(update_fields=['enabled'])
    audit(request, 'Отключён webhook', f'webhook:{pk}')
    return redirect('integrations:index')
