import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import PartnerToken


class RoleJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if auth and auth[0].lower() == b'partner':
            return None
        result = super().authenticate(request)
        if result:
            user, token = result
            profile = getattr(user, 'profile', None)
            if not profile or token.get('active_role') not in profile.roles:
                raise AuthenticationFailed('Роль токена больше не назначена аккаунту.', code='role_revoked')
        return result


class PartnerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != b'partner':
            return None
        if len(auth) != 2:
            raise AuthenticationFailed('Некорректный токен интеграции.')
        digest = hashlib.sha256(auth[1]).hexdigest()
        token = PartnerToken.objects.select_related('created_by__profile').filter(digest=digest).first()
        if not token or not token.usable:
            raise AuthenticationFailed('Токен недействителен, истёк или отозван.')
        PartnerToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token.created_by, token

    def authenticate_header(self, request):
        return 'Partner'


def role(request):
    if isinstance(request.auth, PartnerToken):
        return 'partner'
    if request.auth is not None and hasattr(request.auth, 'get'):
        return request.auth.get('active_role')
    from federation.access import active_role
    return active_role(request)


def require_roles(request, *roles):
    if role(request) not in roles:
        raise PermissionDenied('В активной роли действие недоступно.')


def require_scope(request, scope):
    if isinstance(request.auth, PartnerToken):
        if scope not in request.auth.scopes:
            raise PermissionDenied('Токен не имеет требуемого разрешения.')
    else:
        require_roles(request, 'federation')
