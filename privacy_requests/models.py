from django.conf import settings
from django.db import models


class PrivacyRequest(models.Model):
    class Status(models.TextChoices):
        REQUESTED = 'requested', 'Обращение получено'
        REVIEWING = 'reviewing', 'На рассмотрении'
        APPROVED = 'approved', 'Удаление согласовано'
        REJECTED = 'rejected', 'Мотивированный отказ'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='privacy_requests', verbose_name='Заявитель',
    )
    reason = models.TextField('Комментарий заявителя', max_length=4000, blank=True)
    status = models.CharField('Статус', max_length=12, choices=Status.choices, default=Status.REQUESTED)
    decision_reason = models.TextField('Ответ федерации', max_length=4000, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_privacy_requests', verbose_name='Рассмотрел',
    )
    reviewed_at = models.DateTimeField('Дата рассмотрения', null=True, blank=True)
    created_at = models.DateTimeField('Получено', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = 'Обращение об удалении данных'
        verbose_name_plural = 'Обращения об удалении данных'
        constraints = [models.UniqueConstraint(
            fields=['user'], condition=models.Q(status__in=['requested', 'reviewing', 'approved']),
            name='privacy_one_open_request_per_user',
        )]

    @property
    def can_review(self):
        return self.status in (self.Status.REQUESTED, self.Status.REVIEWING)

    def __str__(self):
        return f'Обращение № {self.pk} · {self.get_status_display()}'


class PrivacyRequestEvent(models.Model):
    request = models.ForeignKey(PrivacyRequest, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField('Статус', max_length=12, choices=PrivacyRequest.Status.choices)
    comment = models.TextField('Комментарий', max_length=4000, blank=True)
    created_at = models.DateTimeField('Дата', auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = 'Запись рассмотрения обращения'
        verbose_name_plural = 'История рассмотрения обращения'
