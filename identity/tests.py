import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from oauth2_provider.models import get_access_token_model, get_application_model, get_grant_model

from federation.models import Discipline, Profile
from .config import signing_key
from .forms import ClientForm
from .validator import DEMO_CLIENT_ID
from .views import validate_id_token


Application = get_application_model()


@override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class IdentityFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('oidc-athlete', 'oidc@example.test', 'TestPass2026!', first_name='Тест', last_name='ФСП')
        self.person = Profile.objects.create(user=self.user, roles=['athlete'], municipality='Демо', birth_date='2000-01-01')
        disc = Discipline.objects.create(name='Демо дисциплина', code='demo-oidc')
        self.person.disciplines.add(disc)
        self.client.force_login(self.user)
        self.redirect_uri = 'https://client.example.test/callback'
        self.application = Application.objects.create(client_id='test-fsp-client', name='Тестовое приложение',
            client_type=Application.CLIENT_PUBLIC, client_secret='', authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            algorithm=Application.RS256_ALGORITHM, redirect_uris=self.redirect_uri)
        self.verifier = 'abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-._~'
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.oauth_override = override_settings(OAUTH2_PROVIDER={**settings.OAUTH2_PROVIDER, 'ALWAYS_RELOAD_OAUTHLIB_CORE': True})
        self.oauth_override.enable()
        self.addCleanup(self.oauth_override.disable)

    def parameters(self, **extra):
        values = {'client_id': self.application.client_id, 'redirect_uri': self.redirect_uri, 'response_type': 'code',
                  'scope': 'openid profile fsp', 'state': 'state-value', 'nonce': 'nonce-value',
                  'code_challenge': self.challenge, 'code_challenge_method': 'S256'}
        values.update(extra)
        return values

    def code(self, **extra):
        data = self.parameters(**extra)
        consent = self.client.get(reverse('oauth2_provider:authorize'), data)
        self.assertEqual(consent.status_code, 200)
        self.assertContains(consent, 'Разрешить передачу данных')
        data['allow'] = 'true'
        response = self.client.post(reverse('oauth2_provider:authorize'), data)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertEqual(query['state'], ['state-value'])
        return query['code'][0]

    def exchange(self, code, verifier=None):
        return self.client.post(reverse('oauth2_provider:token'), {'grant_type': 'authorization_code',
            'client_id': self.application.client_id, 'code': code, 'redirect_uri': self.redirect_uri,
            'code_verifier': verifier or self.verifier})

    def test_complete_consent_pkce_signed_id_token_and_userinfo(self):
        code = self.code()
        response = self.exchange(code)
        self.assertEqual(response.status_code, 200, response.content)
        token = response.json()
        discovery = self.client.get(reverse('oauth2_provider:oidc-connect-discovery-info')).json()
        jwks = self.client.get(reverse('oauth2_provider:jwks-info')).json()
        self.assertEqual(discovery['code_challenge_methods_supported'], ['S256'])
        self.assertNotIn('d', jwks['keys'][0])
        claims = validate_id_token(token['id_token'], jwks, issuer=discovery['issuer'],
                                   audience=self.application.client_id, nonce='nonce-value')
        self.assertEqual(claims['sub'], str(self.user.pk))
        self.assertEqual(claims['roles'], ['athlete'])
        self.assertEqual(claims['disciplines'], ['Демо дисциплина'])
        self.assertNotIn('email', claims)
        self.assertNotIn('birthdate', claims)
        result = self.client.get(reverse('oauth2_provider:user-info'), HTTP_AUTHORIZATION='Bearer ' + token['access_token'])
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['name'], 'Тест ФСП')
        self.assertEqual(result.json()['sub'], claims['sub'])
        # Auth code is single-use.
        self.assertEqual(self.exchange(code).status_code, 400)

    def test_denied_consent_does_not_issue_code(self):
        response = self.client.post(reverse('oauth2_provider:authorize'), self.parameters())
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response['Location']).query)
        self.assertEqual(query['error'], ['access_denied'])
        self.assertNotIn('code', query)
        self.assertEqual(get_grant_model().objects.count(), 0)

    def test_consent_requires_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(reverse('oauth2_provider:authorize'), self.parameters(allow='true')).status_code, 403)

    def test_wrong_pkce_does_not_issue_token(self):
        result = self.exchange(self.code(), verifier='z' * 64)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(get_access_token_model().objects.count(), 0)

    def test_refresh_token_rotates_and_cannot_be_replayed(self):
        token = self.exchange(self.code()).json()
        response = self.client.post(reverse('oauth2_provider:token'), {
            'grant_type': 'refresh_token', 'client_id': self.application.client_id,
            'refresh_token': token['refresh_token'],
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(response.json()['refresh_token'], token['refresh_token'])
        replay = self.client.post(reverse('oauth2_provider:token'), {
            'grant_type': 'refresh_token', 'client_id': self.application.client_id,
            'refresh_token': token['refresh_token'],
        })
        self.assertEqual(replay.status_code, 400)

    def test_missing_pkce_does_not_issue_code(self):
        data = self.parameters()
        data.pop('code_challenge')
        data.pop('code_challenge_method')
        response = self.client.get(reverse('oauth2_provider:authorize'), data)
        self.assertIn(response.status_code, (200, 302, 400))
        self.assertEqual(get_grant_model().objects.count(), 0)
        if response.status_code == 302:
            self.assertIn('error=', response['Location'])

    def test_plain_pkce_is_rejected(self):
        data = self.parameters(code_challenge=self.verifier, code_challenge_method='plain', allow='true')
        response = self.client.post(reverse('oauth2_provider:authorize'), data)
        self.assertIn(response.status_code, (200, 302, 400))
        self.assertEqual(get_grant_model().objects.count(), 0)

    def test_unregistered_and_insecure_redirects_cannot_issue_code(self):
        for uri in ('https://attacker.example.test/callback', 'http://client.example.test/callback'):
            response = self.client.get(reverse('oauth2_provider:authorize'), self.parameters(redirect_uri=uri))
            self.assertIn(response.status_code, (200, 400))
            self.assertEqual(get_grant_model().objects.count(), 0)

    def test_openid_alone_does_not_release_profile_or_roles(self):
        token = self.exchange(self.code(scope='openid')).json()
        response = self.client.get(reverse('oauth2_provider:user-info'), HTTP_AUTHORIZATION='Bearer ' + token['access_token'])
        self.assertEqual(response.json(), {'sub': str(self.user.pk)})

    def test_active_role_controls_client_management(self):
        self.person.roles = ['federation', 'athlete']
        self.person.save()
        session = self.client.session
        session['active_role'] = 'athlete'
        session.save()
        self.assertEqual(self.client.get(reverse('identity:clients')).status_code, 403)
        session['active_role'] = 'federation'
        session.save()
        self.assertEqual(self.client.get(reverse('identity:clients')).status_code, 200)
        response = self.client.post(reverse('identity:client_create'), {'name': 'Внешний клиент', 'redirect_uris': 'https://external.example.test/callback'})
        self.assertEqual(response.status_code, 302)
        app = Application.objects.get(name='Внешний клиент')
        self.assertEqual(app.algorithm, 'RS256')
        self.assertFalse(app.skip_authorization)

    def test_client_form_rejects_http_wildcards_and_fragments(self):
        for value in ('http://localhost/callback', 'https://*.example.test/callback', 'https://example.test/callback#part', 'https://user:pass@example.test/callback'):
            self.assertFalse(ClientForm({'name': 'Клиент', 'redirect_uris': value}).is_valid(), value)

    def test_user_can_revoke_grant_but_not_another_users(self):
        token = self.exchange(self.code()).json()
        record = get_access_token_model().objects.get(user=self.user)
        response = self.client.post(reverse('identity:grant_revoke', args=[record.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('oauth2_provider:user-info'), HTTP_AUTHORIZATION='Bearer ' + token['access_token']).status_code, 401)

    def test_callback_wrong_state_is_rejected_before_token_exchange(self):
        session = self.client.session
        session['oidc_demo'] = {'state': 'right', 'user': self.user.pk, 'created': __import__('time').time()}
        session.save()
        self.assertEqual(self.client.get(reverse('identity:demo_callback'), {'state': 'wrong', 'code': 'fake'}).status_code, 400)

    def test_demo_uses_actual_loopback_server_port(self):
        response = self.client.post(reverse('identity:demo_start'), HTTP_HOST='127.0.0.1:8123', SERVER_PORT='8123')
        self.assertEqual(response.status_code, 302)
        app = Application.objects.get(client_id=DEMO_CLIENT_ID)
        self.assertEqual(app.redirect_uris, 'http://127.0.0.1:8123/identity/demo/callback/')
        bad = self.client.post(reverse('identity:demo_start'), HTTP_HOST='127.0.0.1:8124', SERVER_PORT='8123')
        self.assertEqual(bad.status_code, 403)

    def test_signed_id_token_nonce_and_audience_validation(self):
        token = self.exchange(self.code()).json()
        discovery = self.client.get(reverse('oauth2_provider:oidc-connect-discovery-info')).json()
        jwks = self.client.get(reverse('oauth2_provider:jwks-info')).json()
        with self.assertRaises(ValueError):
            validate_id_token(token['id_token'], jwks, issuer=discovery['issuer'], audience=self.application.client_id, nonce='wrong')
        with self.assertRaises(Exception):
            validate_id_token(token['id_token'], jwks, issuer=discovery['issuer'], audience='wrong-client', nonce='nonce-value')
