from urllib.parse import urlsplit

from django.conf import settings
from oauth2_provider.oauth2_validators import OAuth2Validator


DEMO_CLIENT_ID = 'orenkod-local-demo'


def redirect_is_secure(client_id, uri):
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or '*' in uri:
        return False
    if parsed.scheme == 'https':
        return True
    return (settings.DEBUG and client_id == DEMO_CLIENT_ID and parsed.scheme == 'http'
            and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))


class FederationValidator(OAuth2Validator):
    oidc_claim_scope = {**OAuth2Validator.oidc_claim_scope, 'roles': 'fsp', 'disciplines': 'fsp', 'current_rank': 'fsp'}

    def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
        return redirect_is_secure(client_id, redirect_uri) and super().validate_redirect_uri(client_id, redirect_uri, request, *args, **kwargs)

    def confirm_redirect_uri(self, client_id, code, redirect_uri, client, request, *args, **kwargs):
        return redirect_is_secure(client_id, redirect_uri) and super().confirm_redirect_uri(client_id, code, redirect_uri, client, request, *args, **kwargs)

    def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
        if grant_type not in ('authorization_code', 'refresh_token'):
            return False
        return super().validate_grant_type(client_id, grant_type, client, request, *args, **kwargs)

    def get_additional_claims(self, request):
        person = getattr(request.user, 'profile', None)
        rank = person.current_rank if person else None
        return {'name': request.user.get_full_name() or request.user.username,
                'given_name': request.user.first_name, 'family_name': request.user.last_name,
                'email': request.user.email,
                'roles': person.roles if person else [],
                'disciplines': list(person.disciplines.values_list('name', flat=True)) if person else [],
                'current_rank': rank.requested_title if rank else None}

    def get_discovery_claims(self, request):
        return ['sub', 'name', 'given_name', 'family_name', 'email', 'roles', 'disciplines', 'current_rank']
