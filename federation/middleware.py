from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin
from .access import active_role


class MinistryReadOnlyMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in ('GET', 'HEAD', 'OPTIONS') or not request.user.is_authenticated:
            return None
        name = request.resolver_match.url_name if request.resolver_match else ''
        namespace = request.resolver_match.namespace if request.resolver_match else ''
        if (namespace == 'oauth2_provider' and name == 'authorize') or (namespace == 'identity' and name in ('demo_start', 'grant_revoke')):
            return None
        if active_role(request) == 'ministry' and name not in ('switch_role', 'logout'):
            return HttpResponseForbidden('Кабинет министерства предоставляет доступ только для чтения.')
        return None
