from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from federation.models import Discipline, Organization, Profile


class Stage(models.TextChoices):
    INITIAL = 'initial', 'Начальная подготовка'
    TRAINING = 'training', 'Учебно-тренировочный этап'
    PERFECTION = 'perfection', 'Совершенствование спортивного мастерства'
    MASTERY = 'mastery', 'Высшее спортивное мастерство'


class Sex(models.TextChoices):
    UNSPECIFIED = 'unspecified', 'Не указан'
    FEMALE = 'female', 'Женский'
    MALE = 'male', 'Мужской'


class OrganizationProgram(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='training_programs', verbose_name='Организация')
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, verbose_name='Дисциплина')
    stage = models.CharField('Этап подготовки', max_length=15, choices=Stage.choices)
    license_details = models.CharField('Реквизиты лицензии', max_length=300, blank=True)
    description = models.TextField('Описание программы', blank=True)

    class Meta:
        ordering = ['organization__name', 'discipline__name', 'stage']
        constraints = [models.UniqueConstraint(fields=['organization', 'discipline', 'stage'], name='unique_training_program')]
        verbose_name = 'Программа подготовки'
        verbose_name_plural = 'Программы подготовки'

    def __str__(self):
        return f'{self.organization} · {self.discipline} · {self.get_stage_display()}'


class TrainingGroup(models.Model):
    name = models.CharField('Название группы', max_length=150)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, verbose_name='Организация', related_name='training_groups')
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, verbose_name='Дисциплина', related_name='training_groups')
    stage = models.CharField('Этап подготовки', max_length=15, choices=Stage.choices)
    coach = models.ForeignKey(Profile, on_delete=models.PROTECT, verbose_name='Тренер', related_name='training_groups')
    active = models.BooleanField('Группа действует', default=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Учебно-тренировочная группа'
        verbose_name_plural = 'Учебно-тренировочные группы'

    def clean(self):
        super().clean()
        if self.coach_id:
            if 'coach' not in self.coach.roles or self.coach.status != 'active':
                raise ValidationError({'coach': 'Выберите действующего тренера.'})
            if self.discipline_id and not self.coach.disciplines.filter(pk=self.discipline_id).exists():
                raise ValidationError({'coach': 'У тренера не указана эта дисциплина.'})
        if self.organization_id and self.discipline_id and self.stage:
            if not OrganizationProgram.objects.filter(organization_id=self.organization_id, discipline_id=self.discipline_id, stage=self.stage).exists():
                raise ValidationError('Сначала добавьте программу этой организации по выбранной дисциплине и этапу.')
        if self.pk and self.enrollments.exists():
            original = TrainingGroup.objects.get(pk=self.pk)
            if (original.organization_id, original.discipline_id, original.stage) != (self.organization_id, self.discipline_id, self.stage):
                raise ValidationError('Организация, дисциплина и этап группы с историей зачислений зафиксированы. Создайте новую группу и переведите спортсменов.')

    def __str__(self):
        return f'{self.name} · {self.get_stage_display()}'


class Enrollment(models.Model):
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='training_enrollments', verbose_name='Спортсмен')
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='enrollments', verbose_name='Группа')
    start_date = models.DateField('Дата зачисления', default=timezone.localdate)
    end_date = models.DateField('Последний день занятий', null=True, blank=True)
    municipality = models.CharField('Муниципалитет на момент зачисления', max_length=120)
    sex = models.CharField('Пол для нормативов', max_length=12, choices=Sex.choices, default=Sex.UNSPECIFIED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']
        constraints = [models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F('start_date')), name='training_enrollment_date_order')]
        indexes = [models.Index(fields=['athlete', 'start_date', 'end_date'])]
        verbose_name = 'Зачисление'
        verbose_name_plural = 'История зачислений'

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'Последний день занятий должен быть не раньше зачисления.'})
        if not self.athlete_id or not self.group_id or not self.start_date:
            return
        if 'athlete' not in self.athlete.roles:
            raise ValidationError({'athlete': 'Выберите профиль спортсмена.'})
        if not self.athlete.disciplines.filter(pk=self.group.discipline_id).exists():
            raise ValidationError({'athlete': 'У спортсмена не указана дисциплина группы.'})
        overlapping = Enrollment.objects.filter(athlete_id=self.athlete_id, group__discipline_id=self.group.discipline_id).exclude(pk=self.pk)
        overlapping = overlapping.filter(Q(end_date__isnull=True) | Q(end_date__gte=self.start_date))
        if self.end_date:
            overlapping = overlapping.filter(start_date__lte=self.end_date)
        if overlapping.exists():
            raise ValidationError('У спортсмена уже есть зачисление по этой дисциплине на пересекающиеся даты. Используйте перевод или завершите прошлое зачисление.')
        if self.pk:
            original = Enrollment.objects.get(pk=self.pk)
            if original.athlete_id != self.athlete_id or original.group_id != self.group_id or original.start_date != self.start_date:
                raise ValidationError('Историю зачисления нельзя переписать. Для изменения группы используйте перевод.')

    def __str__(self):
        return f'{self.athlete} · {self.group.name} · с {self.start_date:%d.%m.%Y}'


class Session(models.Model):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name='sessions', verbose_name='Группа')
    date = models.DateField('Дата занятия', default=timezone.localdate)
    topic = models.CharField('Тема занятия', max_length=240)
    format = models.CharField('Формат', max_length=10, choices=[('offline', 'Очно'), ('online', 'Онлайн')], default='offline')
    confirmation_url = models.URLField('Ссылка на запись или лог платформы', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Тренировочное занятие'
        verbose_name_plural = 'Журнал занятий'

    def clean(self):
        super().clean()
        if self.date and self.date > timezone.localdate():
            raise ValidationError({'date': 'В журнал вносятся только состоявшиеся занятия.'})
        if self.format == 'online' and not self.confirmation_url:
            raise ValidationError({'confirmation_url': 'Для онлайн-занятия укажите ссылку на запись или лог платформы.'})
        if self.pk and self.date:
            for attendance in self.attendance.all():
                eligible = Enrollment.objects.filter(group_id=self.group_id, athlete_id=attendance.athlete_id,
                    start_date__lte=self.date).filter(Q(end_date__isnull=True) | Q(end_date__gte=self.date))
                if not eligible.exists():
                    raise ValidationError({'date': 'Новая дата не соответствует зачислению одного из отмеченных спортсменов. Посещаемость нельзя перенести за пределы периода занятий.'})

    def __str__(self):
        return f'{self.group.name} · {self.date:%d.%m.%Y} · {self.topic}'


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='attendance', verbose_name='Занятие')
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='training_attendance', verbose_name='Спортсмен')
    present = models.BooleanField('Присутствовал', default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['session', 'athlete'], name='unique_training_attendance')]
        verbose_name = 'Посещаемость'
        verbose_name_plural = 'Посещаемость'

    def clean(self):
        super().clean()
        if self.session_id and self.athlete_id:
            enrolled = Enrollment.objects.filter(athlete_id=self.athlete_id, group_id=self.session.group_id, start_date__lte=self.session.date).filter(Q(end_date__isnull=True) | Q(end_date__gte=self.session.date))
            if not enrolled.exists():
                raise ValidationError({'athlete': 'Спортсмен не был зачислен в эту группу на дату занятия.'})


class AssessmentNorm(models.Model):
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, verbose_name='Дисциплина')
    stage = models.CharField('Этап подготовки', max_length=15, choices=Stage.choices)
    sex = models.CharField('Пол', max_length=12, choices=[('all', 'Любой')] + Sex.choices, default='all')
    name = models.CharField('Наименование норматива', max_length=160)
    unit = models.CharField('Единица измерения', max_length=50)
    threshold = models.DecimalField('Порог', max_digits=12, decimal_places=3)
    direction = models.CharField('Условие выполнения', max_length=3, choices=[('gte', 'Не менее порога'), ('lte', 'Не более порога')], default='gte')
    required = models.BooleanField('Обязателен для перехода', default=True)
    active = models.BooleanField('Норматив действует', default=True)
    source = models.CharField('Источник и реквизиты', max_length=300, blank=True)
    demonstration = models.BooleanField('Демонстрационный порог', default=False)

    class Meta:
        ordering = ['discipline__name', 'stage', 'name']
        verbose_name = 'Норматив подготовки'
        verbose_name_plural = 'Нормативы подготовки'

    def clean(self):
        super().clean()
        if self.threshold is not None and self.threshold < 0:
            raise ValidationError({'threshold': 'Порог не может быть отрицательным.'})
        if not self.demonstration and not self.source:
            raise ValidationError({'source': 'Для рабочего норматива укажите проверенный источник пороговых значений.'})

    def compare(self, value):
        return value >= self.threshold if self.direction == 'gte' else value <= self.threshold

    def __str__(self):
        return f'{self.name} · {self.discipline} · {self.get_stage_display()}'


class AssessmentResult(models.Model):
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='assessment_results', verbose_name='Спортсмен')
    norm = models.ForeignKey(AssessmentNorm, on_delete=models.PROTECT, related_name='results', verbose_name='Норматив')
    value = models.DecimalField('Результат', max_digits=12, decimal_places=3)
    tested_on = models.DateField('Дата сдачи', default=timezone.localdate)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    threshold_snapshot = models.DecimalField('Порог на дату записи', max_digits=12, decimal_places=3, editable=False, default=0)
    direction_snapshot = models.CharField('Условие на дату записи', max_length=3, editable=False, default='gte')
    norm_snapshot = models.JSONField('Норматив и источник на дату записи', default=dict, blank=True, editable=False)

    class Meta:
        ordering = ['-tested_on', '-id']
        verbose_name = 'Сдача норматива'
        verbose_name_plural = 'История нормативов'

    @property
    def passed(self):
        return self.value >= self.threshold_snapshot if self.direction_snapshot == 'gte' else self.value <= self.threshold_snapshot

    def clean(self):
        super().clean()
        if self.value is not None and self.value < 0:
            raise ValidationError({'value': 'Результат не может быть отрицательным.'})
        if self.tested_on and self.tested_on > timezone.localdate():
            raise ValidationError({'tested_on': 'Дата сдачи не может быть в будущем.'})
        if self.norm_id and self.athlete_id and self.tested_on:
            if not self.norm.active:
                raise ValidationError({'norm': 'Норматив отключён.'})
            enrollments = Enrollment.objects.filter(athlete_id=self.athlete_id, group__discipline_id=self.norm.discipline_id, group__stage=self.norm.stage, start_date__lte=self.tested_on).filter(Q(end_date__isnull=True) | Q(end_date__gte=self.tested_on))
            if self.norm.sex != 'all':
                enrollments = enrollments.filter(sex=self.norm.sex)
            valid_enrollment = enrollments.exists()
            if not valid_enrollment and self.pk:
                # An exam may be taken on the certification day immediately
                # before moving stages; the immutable attestation proves it.
                attestations = Attestation.objects.filter(enrollment__athlete_id=self.athlete_id,
                    enrollment__group__discipline_id=self.norm.discipline_id,
                    enrollment__group__stage=self.norm.stage, date=self.tested_on)
                if self.norm.sex != 'all':
                    attestations = attestations.filter(enrollment__sex=self.norm.sex)
                valid_enrollment = attestations.exists()
            if not valid_enrollment:
                raise ValidationError({'athlete': 'На дату сдачи нет зачисления с подходящими дисциплиной, этапом и полом.'})
            if not self.pk:
                self.capture_norm_snapshot()

    def capture_norm_snapshot(self):
        """Record provenance once; later dictionary edits cannot rewrite it."""
        self.threshold_snapshot, self.direction_snapshot = self.norm.threshold, self.norm.direction
        self.norm_snapshot = {
            'name': self.norm.name, 'unit': self.norm.unit, 'source': self.norm.source,
            'demonstration': self.norm.demonstration, 'discipline_id': self.norm.discipline_id,
            'discipline_name': self.norm.discipline.name, 'stage': self.norm.stage,
            'stage_label': self.norm.get_stage_display(), 'sex': self.norm.sex,
            'required': self.norm.required,
        }

    def save(self, *args, **kwargs):
        if not self.pk and self.norm_id:
            self.capture_norm_snapshot()
        return super().save(*args, **kwargs)


class Attestation(models.Model):
    enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name='attestation', verbose_name='Исходное зачисление')
    destination = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, verbose_name='Новая группа')
    new_enrollment = models.OneToOneField(Enrollment, on_delete=models.PROTECT, related_name='from_attestation', verbose_name='Новое зачисление')
    date = models.DateField('Дата перевода')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    evidence = models.JSONField('Нормативы и результаты на момент аттестации', default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Промежуточная аттестация'
        verbose_name_plural = 'Аттестации и переводы'
