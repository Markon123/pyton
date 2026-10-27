import hashlib
import json
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from federation.models import Competition, Discipline, Result, current_year


class RankCriterion(models.Model):
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT)
    rank_title = models.CharField('Следующий разряд', max_length=100)
    levels = models.JSONField('Допустимые уровни соревнований', default=list)
    max_place = models.PositiveIntegerField('Место не ниже', validators=[MinValueValidator(1)])
    min_score = models.DecimalField('Минимальные баллы', decimal_places=2, max_digits=12, default=0, validators=[MinValueValidator(Decimal('0'))])
    age_min = models.PositiveIntegerField('Возраст от', default=12)
    age_max = models.PositiveIntegerField('Возраст до', default=99)
    source_text = models.TextField('Основание и дополнительные условия')
    source_url = models.URLField('Ссылка на действующий норматив', blank=True)
    active = models.BooleanField('Используется', default=True)
    is_demo = models.BooleanField('Демонстрационный критерий', default=True)

    class Meta:
        ordering = ['discipline__name', 'id']
        verbose_name = 'Критерий кандидата на разряд'
        verbose_name_plural = 'Критерии кандидатов на разряды'

    def clean(self):
        if not self.levels or not isinstance(self.levels, list) or any(v not in dict(Competition.LEVELS) for v in self.levels):
            raise ValidationError({'levels': 'Выберите допустимые уровни соревнований.'})
        if self.age_max < self.age_min or self.age_max > 120:
            raise ValidationError({'age_max': 'Проверьте возрастной диапазон.'})
        if not self.is_demo and not self.source_url:
            raise ValidationError({'source_url': 'Для нормативного критерия необходима ссылка на источник.'})

    def matches(self, result):
        app = result.application
        comp = app.competition
        age = app.athlete.age_on(comp.starts_on)
        return bool(self.active and comp.protocol_published and app.status == 'approved'
                    and app.discipline_id == self.discipline_id and comp.level in self.levels
                    and result.place <= self.max_place and result.score >= self.min_score
                    and age is not None and self.age_min <= age <= self.age_max)

    def __str__(self):
        return f'{self.discipline}: {self.rank_title}'


class Evidence(models.Model):
    INDICATORS = [('participation', 'Численность занимающихся'), ('organizations', 'Организации подготовки'), ('competitions', 'Официальные соревнования')]
    year = models.PositiveIntegerField('Год', default=current_year)
    indicator = models.CharField('Показатель', max_length=30, choices=INDICATORS)
    citation = models.TextField('Источник и пояснение')
    document = models.FileField('Подтверждающий документ', upload_to='evidence/%Y/%m/', blank=True)
    url = models.URLField('Ссылка на источник', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-created_at']
        verbose_name = 'Подтверждение показателя'
        verbose_name_plural = 'Подтверждения показателей'

    def clean(self):
        if self.year < 1900 or self.year > timezone.localdate().year:
            raise ValidationError({'year': 'Укажите допустимый отчётный год.'})


def result_fingerprint(result):
    app = result.application
    comp = app.competition
    value = [result.pk, app.pk, app.status, app.athlete_id, app.athlete.display_name, app.discipline_id,
             comp.pk, comp.title, comp.starts_on.isoformat(), bool(comp.protocol_published),
             result.place, str(result.score)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


class ParticipationCertificate(models.Model):
    number = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    result = models.ForeignKey(Result, on_delete=models.PROTECT, related_name='participation_certificates')
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(auto_now_add=True)
    revoked = models.BooleanField('Отозвана', default=False)
    public_consent = models.BooleanField('Согласие на публичную проверку минимальных сведений')
    fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField('Сведения на дату выдачи', default=dict)

    class Meta:
        ordering = ['-issued_at']
        verbose_name = 'Справка об участии'
        verbose_name_plural = 'Справки об участии'

    @property
    def valid(self):
        return bool(not self.revoked and self.public_consent and self.result.application.status == 'approved'
                    and self.result.application.competition.protocol_published
                    and self.fingerprint == result_fingerprint(self.result))

    @property
    def public_initials(self):
        words = self.snapshot.get('athlete_name', self.result.application.athlete.display_name).split()
        return ' '.join(f'{v[0]}.' for v in words[:3] if v)

    def clean(self):
        if not self.public_consent:
            raise ValidationError({'public_consent': 'Подтвердите согласие на публичную проверку минимальных данных.'})
        if self.result_id and (not self.result.application.competition.protocol_published or self.result.application.status != 'approved'):
            raise ValidationError({'result': 'Нужен результат в опубликованном протоколе.'})

    def __str__(self):
        return str(self.number)
