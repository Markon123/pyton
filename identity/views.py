import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from oauth2_provider.models import get_access_token_model, get_application_model, get_refresh_token_model
from oauth2_provider.views import AuthorizationView

from federation.access import roles_required
from federation.services import audit
from .forms import ClientForm
from .validator import DEMO_CLIENT_ID

Application = get_application_model()
AccessToken = get_access_token_model()
RefreshToken = get_refresh_token_model()


class ConsentView(AuthorizationView):
    template_name = 'identity/consent.html'


@roles_required('federation')
def clients(request):
    return render(request, 'identity/clients.html', {'clients': Application.objects.order_by('name')})


@roles_required('federation')
def client_create(request):
    form = ClientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        app = form.save(commit=False)
        app.user = request.user
        app.client_type = Application.CLIENT_PUBLIC
        app.client_secret = ''
        app.authorization_grant_type = Application.GRANT_AUTHORIZATION_CODE
        app.algorithm = Application.RS256_ALGORITHM
        app.skip_authorization = False
        app.full_clean()
        app.save()
        audit(request, 'Создан клиент ФСП ID', f'oidc-client:{app.pk}', app.name)
        return redirect('identity:clients')
    return render(request, 'identity/client_form.html', {'form': form, 'title': 'Подключить приложение к ФСП ID'}, status=400 if request.method == 'POST' else 200)


@roles_required('federation')
def client_edit(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if app.client_id == DEMO_CLIENT_ID:
        return HttpResponseBadRequest('Адрес тестового клиента настраивается при локальном запуске демонстрации.')
    form = ClientForm(request.POST or None, instance=app)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'Изменён клиент ФСП ID', f'oidc-client:{pk}', app.name)
        return redirect('identity:clients')
    return render(request, 'identity/client_form.html', {'form': form, 'title': 'Настройки приложения'}, status=400 if request.method == 'POST' else 200)


@require_POST
@roles_required('federation')
def client_revoke(request, pk):
    app = get_object_or_404(Application, pk=pk)
    audit(request, 'Отозван клиент ФСП ID', f'oidc-client:{pk}', app.name)
    app.delete()
    messages.success(request, 'Клиент удалён, его токены отозваны.')
    return redirect('identity:clients')


@login_required
def grants(request):
    return render(request, 'identity/grants.html', {'tokens': AccessToken.objects.filter(user=request.user).select_related('application')})


@require_POST
@login_required
def grant_revoke(request, pk):
    token = get_object_or_404(AccessToken, user=request.user, pk=pk)
    # Revoke all credentials for this user/client, including refresh tokens.
    RefreshToken.objects.filter(user=request.user, application=token.application).delete()
    AccessToken.objects.filter(user=request.user, application=token.application).delete()
    audit(request, 'Отозван доступ ФСП ID', 'oidc-grant', 'Отозваны токены приложения')
    return redirect('identity:grants')


@login_required
def demo(request):
    return render(request, 'identity/demo.html', {'claims': request.session.get('oidc_demo_claims'),
                  'userinfo': request.session.get('oidc_demo_userinfo'), 'local_demo_enabled': settings.DEBUG})


def local_demo_origin(request):
    if not settings.DEBUG:
        raise PermissionDenied('Встроенный локальный тестовый клиент отключён в промышленном режиме.')
    origin = request.build_absolute_uri('/').rstrip('/')
    parsed = urlsplit(origin)
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        server_port = int(request.META.get('SERVER_PORT', '0'))
    except ValueError:
        raise PermissionDenied('Не удалось проверить локальный порт.')
    if parsed.hostname not in ('localhost', '127.0.0.1', '::1') or port != server_port:
        raise PermissionDenied('Для локального теста откройте приложение непосредственно на своём компьютере, без внешнего прокси.')
    return origin


@require_POST
@login_required
def demo_start(request):
    origin = local_demo_origin(request)
    callback = origin + reverse('identity:demo_callback')
    app, _ = Application.objects.get_or_create(client_id=DEMO_CLIENT_ID, defaults={
        'name': 'Демонстрационный клиент ФСП ID', 'client_type': Application.CLIENT_PUBLIC,
        'client_secret': '', 'authorization_grant_type': Application.GRANT_AUTHORIZATION_CODE,
        'algorithm': Application.RS256_ALGORITHM, 'skip_authorization': False,
        'redirect_uris': callback,
    })
    # Reserved local client only: bind exact loopback callback to the actual server port.
    app.redirect_uris = callback
    app.save(update_fields=['redirect_uris'])
    verifier = secrets.token_urlsafe(64)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    request.session['oidc_demo'] = {'verifier': verifier, 'state': state, 'nonce': nonce,
                                    'origin': origin, 'callback': callback, 'created': time.time(), 'user': request.user.pk}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
    return redirect(reverse('oauth2_provider:authorize') + '?' + urlencode({
        'client_id': app.client_id, 'redirect_uri': callback, 'response_type': 'code',
        'scope': 'openid profile fsp', 'state': state, 'nonce': nonce,
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'consent',
    }))


def validate_id_token(raw, jwks, *, issuer, audience, nonce):
    """The RP verifies signature and all essential claims before trusting identity."""
    header = jwt.get_unverified_header(raw)
    if header.get('alg') != 'RS256':
        raise ValueError('Тестовый клиент принимает только RS256.')
    keys = [key for key in jwks.get('keys', []) if key.get('kid') == header.get('kid')]
    if len(keys) != 1:
        raise ValueError('Ключ подписи ID token не найден.')
    key = jwt.PyJWK.from_dict(keys[0], algorithm='RS256')
    claims = jwt.decode(raw, key.key, algorithms=['RS256'], audience=audience, issuer=issuer,
                        options={'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce']})
    if not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
        raise ValueError('Не совпадает nonce.')
    return claims


@login_required
def demo_callback(request):
    flow = request.session.pop('oidc_demo', None)
    if not flow or flow.get('user') != request.user.pk or time.time() - flow.get('created', 0) > 600:
        return HttpResponseBadRequest('Тестовый вход истёк. Начните его заново.')
    if not secrets.compare_digest(request.GET.get('state', ''), flow['state']):
        return HttpResponseBadRequest('Не совпадает state запроса.')
    if request.GET.get('error'):
        messages.info(request, 'Вы отменили передачу данных приложению.')
        return redirect('identity:demo')
    code = request.GET.get('code')
    if not code or local_demo_origin(request) != flow['origin']:
        return HttpResponseBadRequest('Неверный ответ тестового входа.')
    try:
        with requests.Session() as transport:
            # This transport only calls the exact, checked loopback origin above.
            transport.trust_env = False
            token_response = transport.post(flow['origin'] + reverse('oauth2_provider:token'), data={
                'grant_type': 'authorization_code', 'client_id': DEMO_CLIENT_ID, 'code': code,
                'redirect_uri': flow['callback'], 'code_verifier': flow['verifier'],
            }, timeout=8, allow_redirects=False)
            token_response.raise_for_status()
            token = token_response.json()
            discovery = transport.get(flow['origin'] + reverse('oauth2_provider:oidc-connect-discovery-info'), timeout=8).json()
            jwks = transport.get(flow['origin'] + reverse('oauth2_provider:jwks-info'), timeout=8).json()
            expected_issuer = settings.OAUTH2_PROVIDER.get('OIDC_ISS_ENDPOINT') or flow['origin']
            if discovery.get('issuer') != expected_issuer:
                raise ValueError('Не совпадает issuer документа обнаружения.')
            claims = validate_id_token(token['id_token'], jwks, issuer=expected_issuer,
                                       audience=DEMO_CLIENT_ID, nonce=flow['nonce'])
            userinfo_response = transport.get(flow['origin'] + reverse('oauth2_provider:user-info'),
                                              headers={'Authorization': 'Bearer ' + token['access_token']}, timeout=8)
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
            if userinfo.get('sub') != claims['sub']:
                raise ValueError('ID token и userinfo относятся к разным пользователям.')
        request.session['oidc_demo_claims'] = claims
        request.session['oidc_demo_userinfo'] = userinfo
        audit(request, 'Проверен вход через ФСП ID', 'oidc-demo', 'PKCE, подпись, issuer, audience, nonce и userinfo проверены')
        messages.success(request, 'Вход через ФСП ID выполнен. Подпись и параметры ответа проверены.')
        return redirect('identity:demo')
    except (requests.RequestException, jwt.PyJWTError, KeyError, ValueError):
        return HttpResponseBadRequest('Тестовый клиент не смог проверить ответ ФСП ID. Проверьте журнал сервера и повторите вход.')
