import hashlib
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


SCOPES = ('read:competitions', 'read:results', 'results:import')
EVENTS = ('competition.opened', 'application.approved', 'protocol.published')


class PartnerToken(models.Model):
    name = models.CharField('Название интеграции', max_length=150)
    digest = models.CharField(max_length=64, unique=True, editable=False)
    prefix = models.CharField(max_length=16, editable=False)
    scopes = models.JSONField('Разрешения', default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField('Действует до')
    revoked = models.BooleanField('Отозван', default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @classmethod
    def issue(cls, *, name, scopes, expires_at, created_by):
        raw = 'fsp_' + secrets.token_urlsafe(32)
        record = cls(name=name, scopes=list(scopes), expires_at=expires_at, created_by=created_by,
                     digest=hashlib.sha256(raw.encode()).hexdigest(), prefix=raw[:12])
        record.full_clean()
        record.save()
        return record, raw

    def clean(self):
        if not self.scopes or any(scope not in SCOPES for scope in self.scopes):
            raise ValidationError({'scopes': 'Выберите допустимые разрешения.'})
        if self.expires_at and self.expires_at <= timezone.now():
            raise ValidationError({'expires_at': 'Дата истечения должна быть в будущем.'})

    @property
    def usable(self):
        profile = getattr(self.created_by, 'profile', None)
        return (not self.revoked and self.expires_at > timezone.now() and self.created_by.is_active
                and profile is not None and 'federation' in profile.roles)


class WebhookEndpoint(models.Model):
    name = models.CharField('Название', max_length=150)
    url = models.URLField('HTTPS URL', max_length=500)
    events = models.JSONField('События', default=list)
    encrypted_key = models.TextField(editable=False)
    enabled = models.BooleanField('Включён', default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        from .services import validate_webhook_url
        validate_webhook_url(self.url, resolve=False)
        if not self.events or any(event not in EVENTS for event in self.events):
            raise ValidationError({'events': 'Выберите поддерживаемые события.'})


class IntegrationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.CharField(max_length=50, choices=[(v, v) for v in EVENTS])
    entity = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ['-created_at']


class WebhookDelivery(models.Model):
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name='deliveries')
    event = models.ForeignKey(IntegrationEvent, on_delete=models.CASCADE, related_name='deliveries')
    status = models.CharField(max_length=12, choices=[('pending', 'Ожидает'), ('sent', 'Доставлено'), ('failed', 'Ошибка')], default='pending')
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['endpoint', 'event'], name='unique_webhook_event_delivery')]
