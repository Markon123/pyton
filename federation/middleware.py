from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from .access import active_role


class ProvisionedAccountMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated:
            return None
        name = request.resolver_match.url_name if request.resolver_match else ''
        if name in ('account_activation', 'logout', 'login', 'health', 'password_change', 'password_change_done'):
            return None
        from .models import ProvisionedAccount
        if ProvisionedAccount.objects.filter(user=request.user, must_change_password=True).exists():
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Завершите первый вход: смените временный пароль и подтвердите согласие.',
                                     'code': 'account_activation_required'}, status=403)
            return redirect('account_activation')


class MinistryReadOnlyMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in ('GET', 'HEAD', 'OPTIONS') or not request.user.is_authenticated:
            return None
        name = request.resolver_match.url_name if request.resolver_match else ''
        namespace = request.resolver_match.namespace if request.resolver_match else ''
        if (namespace == 'oauth2_provider' and name == 'authorize') or (namespace == 'identity' and name in ('demo_start', 'grant_revoke')):
            return None
        # A request concerning one's own data is a personal account action.
        # Review/administration endpoints retain their federation checks.
        if namespace == 'privacy_requests' and name == 'index':
            return None
        if active_role(request) == 'ministry' and name == 'profile':
            from .administration import is_ministry_principal
            if is_ministry_principal(request.user):
                return None
        ministry_actions = ('switch_role', 'logout', 'account_activation', 'federation_accounts',
                            'federation_account_status', 'federation_account_reset')
        if active_role(request) == 'ministry' and name not in ministry_actions:
            return HttpResponseForbidden('Кабинет министерства предоставляет доступ только для чтения.')
        return None
