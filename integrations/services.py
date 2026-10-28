"""Durable integration events and atomic result imports; network only in delivery command."""
import base64
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from federation.models import Application, AuditLog, Competition, Result
from .models import EVENTS, IntegrationEvent, WebhookDelivery, WebhookEndpoint


def encrypt_key(raw):
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key).encrypt(raw.encode()).decode()


def decrypt_key(value):
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key).decrypt(value.encode()).decode()


def validate_webhook_url(url, resolve=False):
    parsed = urlsplit(url)
    allowed = getattr(settings, 'INTEGRATION_WEBHOOK_ALLOWED_HOSTS', [])
    if isinstance(allowed, str):
        allowed = allowed.split(',')
    host = (parsed.hostname or '').lower()
    try:
        port = parsed.port
    except ValueError:
        raise ValidationError('Некорректный порт webhook.')
    if (parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment
            or port not in (None, 443) or host not in [h.lower().strip() for h in allowed]):
        raise ValidationError('Webhook требует HTTPS, порт 443 и точный hostname из INTEGRATION_WEBHOOK_ALLOWED_HOSTS.')
    if not resolve:
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                raise ValidationError('Внутренние адреса для webhook запрещены.')
        except ValueError:
            pass
        return parsed, []
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise ValidationError('Webhook DNS должен разрешаться только в публичные адреса.')
    return parsed, addresses


def emit_event(action, entity, detail='', actor=None):
    event = None
    if action in EVENTS:
        event = action
    elif entity.startswith('competition:') and action == 'Соревнование: publish_competition':
        event = 'competition.published'
    elif entity.startswith('competition:') and (action == 'Соревнование: publish' or 'Опубликован протокол' in action):
        event = 'protocol.published'
    elif entity.startswith('competition:') and (action in ('Соревнование: open', 'Соревнование: reopen') or 'Открыт приём' in action):
        event = 'competition.opened'
    elif entity.startswith('application:') and ('approved' in detail or 'одобрена' in action.lower()):
        event = 'application.approved'
    if not event:
        return None
    record = IntegrationEvent.objects.create(event=event, entity=entity, payload={'entity': entity})
    endpoints = [p for p in WebhookEndpoint.objects.filter(enabled=True) if event in p.events]
    WebhookDelivery.objects.bulk_create([WebhookDelivery(endpoint=p, event=record) for p in endpoints])
    return record


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Pin the DNS-validated IP while preserving hostname SNI and certificate verification."""
    def __init__(self, hostname, address):
        super().__init__(hostname, 443, timeout=5, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def post_webhook(endpoint, body, headers):
    parsed, addresses = validate_webhook_url(endpoint.url, resolve=True)
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    connection = PinnedHTTPSConnection(parsed.hostname, addresses[0])
    try:
        connection.request('POST', path, body, {**headers, 'Host': parsed.hostname})
        response = connection.getresponse()
        response.read(1024)
        if not 200 <= response.status < 300:
            raise RuntimeError(f'HTTP {response.status}; redirects are not followed')
    finally:
        connection.close()


def deliver_pending(limit=50):
    sent = 0
    ids = list(WebhookDelivery.objects.filter(status='pending', next_attempt_at__lte=timezone.now(),
                                              endpoint__enabled=True).values_list('pk', flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            delivery = WebhookDelivery.objects.select_for_update().select_related('endpoint', 'event').get(pk=pk)
            if delivery.status != 'pending' or delivery.next_attempt_at > timezone.now():
                continue
            event, endpoint = delivery.event, delivery.endpoint
            payload = {'id': str(event.pk), 'event': event.event, 'created_at': event.created_at.isoformat(), **event.payload}
            body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            timestamp = str(int(timezone.now().timestamp()))
            delivery.attempts += 1
            try:
                signature = hmac.new(decrypt_key(endpoint.encrypted_key).encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
                post_webhook(endpoint, body, {'Content-Type': 'application/json; charset=utf-8', 'X-FSP-Event-ID': str(event.pk),
                             'X-FSP-Timestamp': timestamp, 'X-FSP-Signature': 'sha256=' + signature})
            except Exception as error:
                # Store only error type/code, never remote response bodies or secrets.
                delivery.last_error = type(error).__name__
                delivery.status = 'failed' if delivery.attempts >= 5 else 'pending'
                delivery.next_attempt_at = timezone.now() + timedelta(seconds=min(86400, 60 * 2 ** delivery.attempts))
            else:
                delivery.status, delivery.sent_at, delivery.last_error = 'sent', timezone.now(), ''
                sent += 1
            delivery.save()
    return sent


def _positive_integer(value, field):
    """Accept integer JSON values and integer CSV cells without truncation."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r'\+?[0-9]+', value.strip()):
        try:
            number = int(value.strip())
        except ValueError:
            raise ValidationError(f'{field}: требуется положительное целое число.')
    else:
        raise ValidationError(f'{field}: требуется положительное целое число.')
    if number < 1:
        raise ValidationError(f'{field}: требуется положительное целое число.')
    return number


def _score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValidationError('score: требуется конечное неотрицательное число.')
    text = str(value).strip()
    if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', text):
        raise ValidationError('score: требуется конечное неотрицательное число.')
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValidationError('score: требуется конечное неотрицательное число.')
    if not number.is_finite() or number < 0:
        raise ValidationError('score: требуется конечное неотрицательное число.')
    return number


@transaction.atomic
def import_results(competition, rows, user):
    competition = Competition.objects.select_for_update().get(pk=competition.pk)
    if competition.protocol_published or competition.state != 'closed':
        raise ValidationError('Импорт доступен только после закрытия заявок и до публикации протокола.')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValidationError('Ожидается от 1 до 10000 результатов.')
    applications = {a.pk: a for a in Application.objects.filter(competition=competition, status='approved')}
    seen, slots, prepared = set(), set(), []
    for row in rows:
        if not isinstance(row, dict) or not {'application', 'place', 'score'}.issubset(row):
            raise ValidationError('Каждая строка требует application, place и score.')
        application_id = _positive_integer(row['application'], 'application')
        place = _positive_integer(row['place'], 'place')
        score = _score(row['score'])
        if application_id in seen or application_id not in applications:
            raise ValidationError('Заявка не одобрена для этого соревнования или повторяется.')
        app = applications[application_id]
        slot = (app.discipline_id, place)
        if slot in slots:
            raise ValidationError('Одно место в дисциплине нельзя назначить дважды.')
        seen.add(application_id)
        slots.add(slot)
        result = Result.objects.filter(application=app).first() or Result(application=app)
        result.place, result.score = place, score
        prepared.append(result)
    # Validate the final placing set, allowing a whole imported table to swap existing places.
    retained = Result.objects.filter(application__competition=competition).exclude(application_id__in=seen)
    occupied = {(r.application.discipline_id, r.place) for r in retained.select_related('application')}
    if occupied.intersection(slots):
        raise ValidationError('Место уже занято участником вне импортируемого набора.')
    for result in prepared:
        result.clean_fields()
        if result.place < 1 or result.score < 0:
            raise ValidationError('Место должно быть положительным, баллы — неотрицательными.')
    for result in prepared:
        result.save()
    AuditLog.objects.create(user=user, role='federation', action='Импорт результатов', entity=f'competition:{competition.pk}', detail=f'{len(prepared)} строк')
    return prepared
