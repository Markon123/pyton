from datetime import date, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from federation.models import Application, AuditLog, Competition, Discipline, Organization, Profile, Result
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
            location='Демо', state='open', regulation='demo.pdf', plan_number='API-DEMO-1')
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

    def test_import_rejects_lossy_integer_values_and_missing_score(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        invalid = [
            {'application': app.pk, 'place': 1.5, 'score': 20},
            {'application': app.pk, 'place': 1.0, 'score': 20},
            {'application': app.pk, 'place': True, 'score': 20},
            {'application': float(app.pk), 'place': 1, 'score': 20},
            {'application': True, 'place': 1, 'score': 20},
            {'application': str(app.pk) + '.0', 'place': 1, 'score': 20},
            {'application': app.pk, 'place': '1.5', 'score': 20},
            {'application': app.pk, 'place': 0, 'score': 20},
            {'application': app.pk, 'place': -1, 'score': 20},
            {'application': app.pk, 'place': 1},
            None,
            ['application', 'place', 'score'],
        ]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                import_results(self.comp, [row], self.fed.user)
            self.assertFalse(Result.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action='Импорт результатов').exists())

    def test_import_rejects_non_finite_negative_or_out_of_precision_scores(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        for score in (None, True, False, '', ' ', 'NaN', 'Infinity', '-Infinity',
                      float('inf'), float('nan'), '1_000', {}, [], -1, '1.001', '100000000'):
            with self.subTest(score=score), self.assertRaises(ValidationError):
                import_results(self.comp, [{'application': app.pk, 'place': 1, 'score': score}], self.fed.user)
            self.assertFalse(Result.objects.exists())

    def test_invalid_later_import_row_preserves_existing_results_and_audit(self):
        app, other = self.approved(), self.approved(self.other)
        original = Result.objects.create(application=app, place=2, score='45.50')
        self.comp.state = 'closed'
        self.comp.save()
        with self.assertRaises(ValidationError):
            import_results(self.comp, [
                {'application': app.pk, 'place': 1, 'score': 200},
                {'application': other.pk, 'place': 2.5, 'score': 100},
            ], self.fed.user)
        original.refresh_from_db()
        self.assertEqual((original.place, original.score), (2, Decimal('45.50')))
        self.assertEqual(Result.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(action='Импорт результатов').exists())

    def test_import_accepts_integer_csv_cells_and_atomic_place_swaps(self):
        app, other = self.approved(), self.approved(self.other)
        first = Result.objects.create(application=app, place=1, score=100)
        second = Result.objects.create(application=other, place=2, score=90)
        self.comp.state = 'closed'
        self.comp.save()
        imported = import_results(self.comp, [
            {'application': f' {app.pk} ', 'place': '2', 'score': '142.50'},
            {'application': str(other.pk), 'place': '+1', 'score': '0'},
        ], self.fed.user)
        self.assertEqual(len(imported), 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.place, first.score), (2, Decimal('142.50')))
        self.assertEqual((second.place, second.score), (1, Decimal('0.00')))

    def test_api_invalid_import_returns_validation_error_without_writes(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        client, _ = self.auth(self.fed)
        for row in ({'application': app.pk, 'place': 1.5},
                    {'application': app.pk, 'place': 1, 'score': 'NaN'}):
            with self.subTest(row=row):
                response = client.post(f'/api/v1/competitions/{self.comp.pk}/results/import/',
                                       {'results': [row]}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data['code'], 'validation_error')
                self.assertFalse(Result.objects.exists())

    def test_csv_upload_imports_real_numeric_results(self):
        app = self.approved()
        self.comp.state = 'closed'
        self.comp.save()
        self.client.force_login(self.fed.user)
        uploaded = SimpleUploadedFile('results.csv',
            f'application,place,score\n{app.pk},1,142.50\n'.encode(), content_type='text/csv')
        response = self.client.post('/integrations/', {'action': 'import', 'competition': self.comp.pk, 'file': uploaded})
        self.assertEqual(response.status_code, 302)
        result = Result.objects.get(application=app)
        self.assertEqual((result.place, result.score), (1, Decimal('142.50')))

    def test_census_api_applies_municipality_and_age_filters(self):
        self.athlete.municipality = 'Оренбург'
        self.athlete.birth_date = date(self.today.year - 30, 1, 1)
        self.athlete.save()
        self.other.municipality = 'Орск'
        self.other.birth_date = date(self.today.year - 15, 1, 1)
        self.other.save()
        client, _ = self.auth(self.fed)
        years = {'year_from': self.today.year - 1, 'year_to': self.today.year}
        cases = [
            ({'municipality': 'Не существует'}, [0, 0]),
            ({'municipality': 'Оренбург'}, [1, 1]),
            ({'municipality': 'Оренбург', 'age_max': 25}, [0, 0]),
            ({'age_min': 10, 'age_max': 18}, [1, 1]),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                response = client.get('/api/v1/reports/athletes-count/', {**years, **params})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([row['total'] for row in response.data['results']], expected)
        ministry, _ = self.auth(self.ministry)
        response = ministry.get('/api/v1/reports/athletes-count/', {**years, 'municipality': 'Не существует'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['total'] for row in response.data['results']], [0, 0])

    def test_profile_api_uses_unicode_full_name_and_organization_search(self):
        self.athlete.user.first_name = 'Александр'
        self.athlete.user.last_name = 'Тестов'
        self.athlete.user.save()
        self.athlete.organization = Organization.objects.create(name='Код Оренбуржья', municipality='Оренбург')
        self.athlete.save()
        client, _ = self.auth(self.fed)
        for query in ('александр тестов', 'ТЕСТОВ АЛЕКСАНДР', 'код оренбуржья'):
            with self.subTest(query=query):
                response = client.get('/api/v1/athletes/', {'q': query})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([person['id'] for person in response.data['results']], [self.athlete.pk])

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
        publication = emit_event('Соревнование: publish_competition', f'competition:{self.comp.pk}')
        self.assertEqual(publication.event, 'competition.published')
        self.assertFalse(publication.deliveries.exists())
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

    @override_settings(INTEGRATION_WEBHOOK_ALLOWED_HOSTS=['hooks.example.test'])
    def test_real_publication_queues_its_own_webhook_event(self):
        endpoint = WebhookEndpoint.objects.create(name='Календарь', url='https://hooks.example.test/events',
            events=['competition.published'], encrypted_key=encrypt_key('publication-test-key'), created_by=self.fed.user)
        endpoint.full_clean()
        self.comp.state = 'draft'
        self.comp.save()
        client, _ = self.auth(self.fed)
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/publish/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        event = IntegrationEvent.objects.get(event='competition.published', entity=f'competition:{self.comp.pk}')
        self.assertEqual(list(event.deliveries.values_list('endpoint_id', flat=True)), [endpoint.pk])
        self.assertFalse(IntegrationEvent.objects.filter(event='protocol.published').exists())
        response = client.post(f'/api/v1/competitions/{self.comp.pk}/open-applications/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(IntegrationEvent.objects.filter(event='competition.opened').count(), 1)
        self.assertEqual(WebhookDelivery.objects.count(), 1)

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
        paths = schema.data['paths']
        census_parameters = {p['name'] for p in paths['/api/v1/reports/athletes-count/']['get']['parameters']}
        self.assertTrue({'municipality', 'age_min', 'age_max'}.issubset(census_parameters))
        import_body = paths['/api/v1/competitions/{id}/results/import/']['post']['requestBody']
        imported_row = import_body['content']['application/json']['schema']['properties']['results']['items']
        self.assertEqual(set(imported_row['required']), {'application', 'place', 'score'})
        self.assertNotEqual(paths['/api/v1/auth/logout/']['post']['security'], [])
        self.assertIn('/api/v1/competitions/{id}/protocol/', paths)
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
