from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from federation.models import Competition, Profile, Result


class ViolationRecord(models.Model):
    DECISIONS = [('warning', 'Предупреждение'), ('disqualification', 'Дисквалификация'), ('technical_defeat', 'Техническое поражение')]
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='violation_records')
    competition = models.ForeignKey(Competition, on_delete=models.PROTECT, related_name='violation_records')
    reason = models.TextField('Обоснование решения')
    evidence_url = models.URLField('Ссылка на доказательства', blank=True)
    evidence_file = models.FileField('Доказательства', upload_to='violations/%Y/%m/', blank=True)
    decision = models.CharField('Решение', max_length=24, choices=DECISIONS)
    starts_on = models.DateField('Начало действия', default=timezone.localdate)
    ends_on = models.DateField('Окончание действия', null=True, blank=True)
    status = models.CharField(max_length=12, choices=[('confirmed', 'Действует'), ('overturned', 'Отменено по апелляции')], default='confirmed')
    previous_until = models.DateField(null=True, blank=True, editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        if self.athlete_id and 'athlete' not in self.athlete.roles:
            raise ValidationError({'athlete': 'Нарушение фиксируется для спортсмена.'})
        if self.decision == 'disqualification' and not self.ends_on:
            raise ValidationError({'ends_on': 'Для дисквалификации нужен срок окончания.'})
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({'ends_on': 'Окончание не может быть раньше начала.'})

    def __str__(self):
        return f'{self.athlete} · {self.get_decision_display()}'


class Appeal(models.Model):
    STATUSES = [('pending', 'Подана'), ('reviewed', 'На рассмотрении'), ('rejected', 'Отклонена'), ('upheld', 'Удовлетворена')]
    result = models.ForeignKey(Result, on_delete=models.PROTECT, null=True, blank=True, related_name='appeals')
    violation = models.ForeignKey(ViolationRecord, on_delete=models.PROTECT, null=True, blank=True, related_name='appeals')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='appeals')
    reason = models.TextField('Основание апелляции')
    evidence_url = models.URLField('Ссылка на доказательства', blank=True)
    evidence_file = models.FileField('Доказательства', upload_to='appeals/%Y/%m/', blank=True)
    status = models.CharField('Статус', max_length=12, choices=STATUSES, default='pending')
    decision_reason = models.TextField('Мотивированное решение', blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='reviewed_appeals')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=(models.Q(result__isnull=False, violation__isnull=True) | models.Q(result__isnull=True, violation__isnull=False)), name='appeal_exactly_one_source')]

    @property
    def athlete(self):
        return self.result.application.athlete if self.result_id else self.violation.athlete

    @property
    def competition(self):
        return self.result.application.competition if self.result_id else self.violation.competition

    def clean(self):
        if bool(self.result_id) == bool(self.violation_id):
            raise ValidationError('Выберите ровно один источник: результат или решение о нарушении.')
        if self.result_id and (not self.result.application.competition.protocol_published or self.result.application.status != 'approved'):
            raise ValidationError({'result': 'Апелляция подаётся на результат опубликованного протокола.'})
        if self.status in ('rejected', 'upheld') and not self.decision_reason.strip():
            raise ValidationError({'decision_reason': 'Для решения необходимо обоснование.'})

    def __str__(self):
        return f'Апелляция №{self.pk}: {self.athlete}'


class ProtocolAmendment(models.Model):
    competition = models.ForeignKey(Competition, on_delete=models.PROTECT, related_name='amendments')
    result = models.ForeignKey(Result, on_delete=models.PROTECT, related_name='amendments')
    appeal = models.OneToOneField(Appeal, on_delete=models.PROTECT, related_name='amendment')
    old_value = models.JSONField()
    new_value = models.JSONField()
    reason = models.TextField()
    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AwardRule(models.Model):
    level = models.CharField('Уровень соревнования', max_length=25, choices=Competition.LEVELS)
    place = models.PositiveIntegerField('Место')
    amount = models.DecimalField('Сумма, руб.', max_digits=12, decimal_places=2)
    source = models.CharField('Источник / нормативный акт', max_length=240, default='Демонстрационные суммы; требуется настройка по действующему НПА')
    demonstration = models.BooleanField('Демонстрационное правило', default=True)
    active = models.BooleanField('Применять', default=True)

    class Meta:
        ordering = ['level', 'place']
        constraints = [models.UniqueConstraint(fields=['level', 'place'], name='unique_award_level_place'), models.CheckConstraint(condition=models.Q(amount__gte=0), name='award_nonnegative_amount')]

    def clean(self):
        if self.place is not None and not 1 <= self.place <= 3:
            raise ValidationError({'place': 'Премии назначаются за места 1–3.'})
        if self.amount is not None and self.amount < Decimal('0'):
            raise ValidationError({'amount': 'Сумма не может быть отрицательной.'})

    def __str__(self):
        return f'{self.get_level_display()}, {self.place} место · {self.amount} руб.'


class AwardPetition(models.Model):
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='award_petitions')
    year = models.PositiveIntegerField('Год результатов')
    result = models.ForeignKey(Result, on_delete=models.PROTECT)
    rule = models.ForeignKey(AwardRule, on_delete=models.PROTECT)
    amount = models.DecimalField('Сумма, руб.', max_digits=12, decimal_places=2)
    provenance = models.JSONField(default=dict)
    status = models.CharField('Статус', max_length=12, choices=[('prepared', 'Подготовлено'), ('sent', 'Направлено'), ('awarded', 'Премия назначена')], default='prepared')
    passport_mask = models.CharField('Паспорт (маска)', max_length=40, default='** ** ******')
    inn_mask = models.CharField('ИНН (маска)', max_length=40, default='************')
    snils_mask = models.CharField('СНИЛС (маска)', max_length=40, default='***-***-*** **')
    account_mask = models.CharField('Счёт (маска)', max_length=40, default='********************')
    comment = models.TextField('Комментарий', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', 'athlete__user__last_name']
        constraints = [models.UniqueConstraint(fields=['athlete', 'year'], name='unique_athlete_year_petition')]

    @property
    def deadline(self):
        from datetime import date
        return date(self.year + 1, 2, 1)

    def clean(self):
        for key in ('passport_mask', 'inn_mask', 'snils_mask', 'account_mask'):
            value = getattr(self, key)
            if not value or '*' not in value or any(c.isdigit() for c in value):
                raise ValidationError({key: 'В демоверсии допустимы только маски без цифр (например, ************).'})
        if self.result_id and self.athlete_id and self.result.application.athlete_id != self.athlete_id:
            raise ValidationError({'result': 'Результат принадлежит другому спортсмену.'})

    def __str__(self):
        return f'{self.athlete} · {self.year} · {self.amount} руб.'
