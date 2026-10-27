import uuid

from django.conf import settings
from django.db import models


FORMATS = [('xlsx', 'Excel · XLSX'), ('docx', 'Word · DOCX'), ('pdf', 'PDF')]


class ReportJob(models.Model):
    STATES = [('queued', 'В очереди'), ('running', 'Формируется'), ('ready', 'Готов'), ('failed', 'Ошибка')]
    number = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='report_jobs')
    params = models.JSONField('Фильтры отчёта')
    format = models.CharField('Формат', max_length=5, choices=FORMATS, default='xlsx')
    status = models.CharField('Состояние', max_length=10, choices=STATES, default='queued')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)
    filename = models.CharField(max_length=100, blank=True)
    error = models.CharField('Ошибка', max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['status', 'lease_expires_at'])]
        verbose_name = 'Фоновый отчёт'
        verbose_name_plural = 'Фоновые отчёты'

    def clean(self):
        from .forms import validate_saved_params
        validate_saved_params(self.params, self.format)

    def __str__(self):
        return f'{self.number} · {self.get_status_display()}'
