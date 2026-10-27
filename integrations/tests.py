from datetime import date, timedelta
import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from federation.models import Application, AuditLog, Competition, Discipline, Profile, Result
from .models import IntegrationEvent, PartnerToken, WebhookDelivery, WebhookEndpoint
from .services import deliver_pending, emit_event, encrypt_key, import_results, validate_webhook_url


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class APIIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.discipline = Discipline.objects.create(name='Алгоритмы', code='api-algorithms')
        cls.fed = cls.person('api-fed', ['federation', 'athlete'])
        cls.athlete = cls.person('api-athlete', ['athlete'])
        cls.other = cls.person('api-other', ['athlete'])
        cls.ministry = cls.person('api-ministry', ['ministry'])
        cls.comp = Competition.objects.create(title='API demo', starts_on=cls.today, ends_on=cls.today,
            registration_opens=cls.today - timedelta(days=3), registration_closes=cls.today,
            location='Демо', state='open', regulation='demo.pdf')
        cls.comp.disciplines.add(cls.discipline)

    @classmethod
    def person(cls, name, roles):
        user = get_user_model().objects.create_user(name, name+'@example.test', 'ApiDemoPass2026!')
        profile = Profile.objects.create(user=user, roles=roles, birth_date=date(2000, 1, 1), start_year=2020)
        profile.disciplines.add(cls.discipline)
        return profile

    def auth(self, person, selected=None):
        client = APIClient()
        response = client.post('/api/v1/auth/login/', {'username': person.user.username, 'password': 'ApiDemoPass2026!',
                                                      'role': selected or person.roles[0]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer '+response.data['access'])
        return client, response.data

    def approved(self, person=None):
        return Application.objects.create(competition=self.comp, athlete=person or self.athlete,
            discipline=self.discipline, submitted_by=(person or self.athlete).user, status='approved')

    def test_jwt_full_competition_cycle_and_audit_active_role(self):
        athlete, _ = self.auth(self.athlete)
        response = athlete.post(f'/api/v1/competitions/{self.comp.pk}/applications/',
            {'athlete': self.athlete.pk, 'discipline': self.discipline.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        app_id = response.data['id']
        fed, _ = self.auth(self.fed)
        response = fed.patch(f'/api/v1/applications/{app_id}/moderate/', {'status': 'approved'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = fed.post(f'/api/v1/competitions/{self.comp.pk}/close-applications/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = fed.post(f'/api/v1/competitions/{self.comp.pk}/results/',
            {'application': app_id, 'place': 1, 'score': '100.5'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = fed.post(f'/api/v1/competitions/{self.comp.pk}/publish-protocol/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        result = athlete.get(f'/api/v1/competitions/{self.comp.pk}/protocol/')
        self.assertTrue(result.data['published'])
        self.assertEqual(result.data['results'][0]['place'], 1)
        self.assertTrue(AuditLog.objects.filter(user=self.athlete.user, role='athlete', action='Подана заявка').exists())
        response = fed.post(f'/api/v1/competitions/{self.comp.pk}/results/',
            {'application': app_id, 'place': 2, 'score': 1}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Result.objects.get(application_id=app_id).place, 1)

    def test_active_jwt_role_does_not_inherit_federation_privileges(self):
        client, _ = self.auth(self.fed, 'athlete')
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/close-applications/', {}, format='json')
        self.assertEqual(response.status_code, 403)
        changed = client.post('/api/v1/auth/switch-role/', {'role': 'federation'}, format='json')
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data['active_role'], 'federation')
        denied = client.post('/api/v1/auth/switch-role/', {'role': 'ministry'}, format='json')
        self.assertEqual(denied.status_code, 403)

    def test_refresh_rotation_logout_and_malformed_tokens(self):
        client, tokens = self.auth(self.athlete)
        refreshed = APIClient().post('/api/v1/auth/refresh/', {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        duplicate = APIClient().post('/api/v1/auth/refresh/', {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(duplicate.status_code, 401)
        logged_out = client.post('/api/v1/auth/logout/', {'refresh': refreshed.data['refresh']}, format='json')
        self.assertEqual(logged_out.status_code, 200)
        invalid = APIClient().post('/api/v1/auth/refresh/', {'refresh': 'broken'}, format='json')
        self.assertEqual(invalid.status_code, 401)

    def test_revoked_role_invalidates_access_and_refresh(self):
        client, tokens = self.auth(self.athlete)
        self.athlete.roles = []
        self.athlete.save(update_fields=['roles'])
        self.assertEqual(client.get('/api/v1/me/').status_code, 401)
        response = APIClient().post('/api/v1/auth/refresh/', {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_application_identity_forgery_and_ministry_write_denied(self):
        client, _ = self.auth(self.athlete)
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/applications/',
                              {'athlete': self.other.pk, 'discipline': self.discipline.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Application.objects.exists())
        ministry, _ = self.auth(self.ministry)
        self.assertEqual(ministry.get('/api/v1/competitions/').status_code, 200)
        self.assertEqual(ministry.post('/api/v1/competitions/', {}, format='json').status_code, 403)
        self.assertEqual(ministry.get('/api/v1/athletes/').status_code, 403)

    def test_private_profile_not_exposed_by_public_registry(self):
        client, _ = self.auth(self.athlete)
        listing = client.get('/api/v1/athletes/')
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn('birth_date', listing.data['results'][0])
        self.assertEqual(client.get(f'/api/v1/athletes/{self.other.pk}/').status_code, 404)
        self.assertIn('birth_date', client.get('/api/v1/me/').data)

    def test_partner_token_is_hashed_scoped_expiring_and_revocable(self):
        record, raw = PartnerToken.issue(name='Партнёр', scopes=['read:competitions'],
            expires_at=timezone.now()+timedelta(days=1), created_by=self.fed.user)
        self.assertNotEqual(raw, record.digest)
        self.assertEqual(record.digest, hashlib.sha256(raw.encode()).hexdigest())
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Partner '+raw)
        self.assertEqual(client.get('/api/v1/competitions/').status_code, 200)
        self.assertEqual(client.get('/api/v1/athletes/').status_code, 403)
        self.assertEqual(client.post(f'/api/v1/competitions/{self.comp.pk}/results/import/', {'results': []}, format='json').status_code, 403)
        record.revoked = True
        record.save()
        self.assertEqual(client.get('/api/v1/competitions/').status_code, 401)

    def test_import_validates_whole_table_before_writing(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        with self.assertRaises(ValidationError):
            import_results(self.comp, [{'application': app.pk, 'place': 1, 'score': 100},
                                      {'application': 999999, 'place': 2, 'score': 50}], self.fed.user)
        self.assertFalse(Result.objects.exists())
        results = import_results(self.comp, [{'application': app.pk, 'place': 1, 'score': 100}], self.fed.user)
        self.assertEqual(len(results), 1)
        self.assertEqual(Result.objects.get().score, 100)
        self.comp.protocol_published = True
        self.comp.save()
        with self.assertRaises(ValidationError):
            import_results(self.comp, [{'application': app.pk, 'place': 2, 'score': 1}], self.fed.user)
        self.assertEqual(Result.objects.get().place, 1)

    def test_partner_import_scope_works_for_approved_applications(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        _, raw = PartnerToken.issue(name='Импорт', scopes=['results:import'], expires_at=timezone.now()+timedelta(days=1), created_by=self.fed.user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Partner '+raw)
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/results/import/',
                               {'results': [{'application': app.pk, 'place': 1, 'score': 80}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['imported'], 1)

    def test_csrf_required_for_session_authentication(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.fed.user)
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/close-applications/', {}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_registration_cannot_assign_ministry_or_federation(self):
        for selected in ['federation', 'ministry']:
            response = APIClient().post('/api/v1/auth/register/', {'roles': [selected]}, format='json')
            self.assertEqual(response.status_code, 400)

    @override_settings(INTEGRATION_WEBHOOK_ALLOWED_HOSTS=['hooks.example.test'])
    def test_event_mapping_signature_queue_and_retry(self):
        endpoint = WebhookEndpoint.objects.create(name='Тест', url='https://hooks.example.test/events',
            events=['protocol.published'], encrypted_key=encrypt_key('test-signing-secret'), created_by=self.fed.user)
        self.assertIsNone(emit_event('Соревнование: publish_competition', f'competition:{self.comp.pk}'))
        event = emit_event('Соревнование: publish', f'competition:{self.comp.pk}')
        self.assertEqual(event.event, 'protocol.published')
        self.assertEqual(WebhookDelivery.objects.count(), 1)
        with patch('integrations.services.post_webhook', side_effect=RuntimeError('server down')):
            self.assertEqual(deliver_pending(), 0)
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.status, 'pending')
        self.assertEqual(deliver_pending(), 0)
        delivery.next_attempt_at = timezone.now()-timedelta(seconds=1)
        delivery.save()
        with patch('integrations.services.post_webhook') as post:
            self.assertEqual(deliver_pending(), 1)
        endpoint_arg, body, headers = post.call_args.args
        expected = hmac.new(b'test-signing-secret', headers['X-FSP-Timestamp'].encode()+b'.'+body, hashlib.sha256).hexdigest()
        self.assertEqual(headers['X-FSP-Signature'], 'sha256='+expected)
        self.assertEqual(json.loads(body)['id'], str(event.pk))
        self.assertEqual(deliver_pending(), 0)

    @override_settings(INTEGRATION_WEBHOOK_ALLOWED_HOSTS=['hooks.example.test', '127.0.0.1'])
    def test_webhook_blocks_unlisted_hosts_private_dns_and_redirect_style_urls(self):
        for url in ['http://hooks.example.test/', 'https://evil.example/', 'https://u:p@hooks.example.test/',
                    'https://hooks.example.test:8443/', 'https://127.0.0.1/']:
            with self.assertRaises(ValidationError):
                validate_webhook_url(url)
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValidationError):
                validate_webhook_url('https://hooks.example.test/', resolve=True)

    def test_api_schema_and_integration_console(self):
        schema = APIClient().get('/api/v1/schema/')
        self.assertEqual(schema.status_code, 200)
        self.assertEqual(schema.data['openapi'], '3.0.3')
        self.client.force_login(self.fed.user)
        self.assertEqual(self.client.get('/integrations/').status_code, 200)

    def test_invalid_action_is_an_error_not_a_success_response(self):
        client, _ = self.auth(self.fed)
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/actions/unknown-action/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.state, 'open')

    def test_dictionary_api_rejects_non_object_metadata(self):
        from federation.models import DictionaryEntry
        client, _ = self.auth(self.fed)
        response = client.post('/api/v1/dictionaries/entries/', {'kind':'rank','code':'invalid-json','label':'Некорректный','metadata':['not-object'],'active':True}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DictionaryEntry.objects.filter(code='invalid-json').exists())
