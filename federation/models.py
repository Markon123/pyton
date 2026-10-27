from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def current_year():
    return timezone.localdate().year


class Role(models.TextChoices):
    FEDERATION = 'federation', 'Федерация'
    ATHLETE = 'athlete', 'Спортсмен'
    COACH = 'coach', 'Тренер'
    JUDGE = 'judge', 'Судья'
    MINISTRY = 'ministry', 'Министерство · чтение'


class Discipline(models.Model):
    name = models.CharField('Название', max_length=160)
    code = models.SlugField('Код', unique=True, max_length=40)

    class Meta:
        ordering = ['id']
        verbose_name = 'Дисциплина'
        verbose_name_plural = 'Дисциплины'

    def __str__(self):
        return self.name


class Organization(models.Model):
    name = models.CharField('Название', max_length=200)
    kind = models.CharField('Тип', max_length=100, default='Клуб')
    municipality = models.CharField('Муниципалитет', max_length=120)

    class Meta:
        ordering = ['name']
        verbose_name = 'Организация'
        verbose_name_plural = 'Организации'

    def __str__(self):
        return self.name


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    roles = models.JSONField('Роли', default=list)
    birth_date = models.DateField('Дата рождения', null=True, blank=True)
    municipality = models.CharField('Муниципалитет', max_length=120, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name='profiles')
    disciplines = models.ManyToManyField(Discipline, verbose_name='Дисциплины', blank=True, related_name='profiles')
    start_year = models.PositiveIntegerField('Год начала занятий', default=current_year)
    end_year = models.PositiveIntegerField('Год окончания занятий', null=True, blank=True)
    status = models.CharField('Статус', max_length=12, choices=[('active', 'Активен'), ('archived', 'Архив')], default='active')
    disqualified_until = models.DateField('Отстранён до', null=True, blank=True)
    guardian_name = models.CharField('Законный представитель', max_length=200, blank=True)
    anti_doping_date = models.DateField('Дата курса РУСАДА', null=True, blank=True)
    anti_doping_url = models.URLField('Сертификат РУСАДА', blank=True)

    class Meta:
        ordering = ['user__last_name', 'user__first_name', 'id']
        verbose_name = 'Профиль'
        verbose_name_plural = 'Профили'
        constraints = [models.CheckConstraint(condition=models.Q(end_year__isnull=True) | models.Q(end_year__gte=models.F('start_year')), name='profile_year_order')]

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def age(self):
        return self.age_on(timezone.localdate())

    def age_on(self, day):
        if not self.birth_date:
            return None
        return day.year - self.birth_date.year - ((day.month, day.day) < (self.birth_date.month, self.birth_date.day))

    @property
    def current_rank(self):
        return self.rank_applications.filter(kind='rank', status='approved', valid_until__gte=timezone.localdate()).order_by('-assigned_on', '-id').first()

    @property
    def current_category(self):
        return self.rank_applications.filter(kind='category', status='approved', valid_until__gte=timezone.localdate()).order_by('-assigned_on', '-id').first()

    def clean(self):
        super().clean()
        if not isinstance(self.roles, list) or any(role not in Role.values for role in self.roles):
            raise ValidationError({'roles': 'Указаны неизвестные роли.'})
        if self.birth_date and self.birth_date > timezone.localdate():
            raise ValidationError({'birth_date': 'Дата рождения не может быть в будущем.'})
        if self.start_year > current_year():
            raise ValidationError({'start_year': 'Год начала занятий не может быть в будущем.'})

    def __str__(self):
        return self.display_name


class Consent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='consents')
    kind = models.CharField('Вид согласия', max_length=20, choices=[('personal_data', 'Обработка ПДн'), ('guardian', 'Согласие представителя')])
    representative_name = models.CharField('Законный представитель', max_length=200, blank=True)
    version = models.CharField('Версия документа', max_length=30, default='1.0')
    accepted_at = models.DateTimeField('Принято', auto_now_add=True)

    class Meta:
        ordering = ['-accepted_at']
        verbose_name = 'Согласие'
        verbose_name_plural = 'Согласия'

    def __str__(self):
        return f'{self.user}: {self.get_kind_display()}'


class Competition(models.Model):
    LEVELS = [
        ('regional', 'Региональное'), ('interregional', 'Межрегиональное'),
        ('russian_championship', 'Чемпионат России'), ('russian_junior', 'Первенство России'),
    ]
    STATES = [('draft', 'Черновик'), ('published', 'Опубликовано'), ('open', 'Приём заявок'), ('closed', 'Заявки закрыты'), ('finished', 'Завершено')]
    title = models.CharField('Название', max_length=240)
    description = models.TextField('Описание', blank=True)
    disciplines = models.ManyToManyField(Discipline, related_name='competitions', verbose_name='Дисциплины')
    starts_on = models.DateField('Начало')
    ends_on = models.DateField('Окончание')
    registration_opens = models.DateField('Начало регистрации')
    registration_closes = models.DateField('Конец регистрации')
    location = models.CharField('Место', max_length=240)
    format = models.CharField('Формат', max_length=10, choices=[('offline', 'Очно'), ('online', 'Онлайн'), ('hybrid', 'Смешанный')], default='offline')
    level = models.CharField('Уровень', max_length=25, choices=LEVELS, default='regional')
    kind = models.CharField('Тип', max_length=100, default='Чемпионат')
    age_min = models.PositiveIntegerField('Возраст от', default=12)
    age_max = models.PositiveIntegerField('Возраст до', default=99)
    calendar_plan = models.CharField('Календарный план', max_length=20, choices=[('regional', 'КП Оренбургской области'), ('ekp', 'ЕКП Минспорта России')], default='regional')
    plan_number = models.CharField('Номер в плане', max_length=80, blank=True)
    regulation = models.FileField('Положение', upload_to='regulations/%Y/%m/', blank=True)
    state = models.CharField('Состояние', max_length=12, choices=STATES, default='draft')
    protocol_published = models.BooleanField('Протокол опубликован', default=False)

    class Meta:
        ordering = ['starts_on', 'id']
        verbose_name = 'Соревнование'
        verbose_name_plural = 'Соревнования'
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_on__gte=models.F('starts_on')), name='competition_date_order'),
            models.CheckConstraint(condition=models.Q(registration_closes__gte=models.F('registration_opens')), name='competition_registration_order'),
            models.CheckConstraint(condition=models.Q(age_max__gte=models.F('age_min')), name='competition_age_order'),
        ]

    @property
    def is_registration_open(self):
        return self.state == 'open' and self.registration_opens <= timezone.localdate() <= self.registration_closes

    def clean(self):
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({'ends_on': 'Окончание должно быть не раньше начала.'})
        if self.registration_opens and self.registration_closes and self.registration_closes < self.registration_opens:
            raise ValidationError({'registration_closes': 'Окончание регистрации должно быть не раньше её начала.'})
        if self.registration_closes and self.starts_on and self.registration_closes > self.starts_on:
            raise ValidationError({'registration_closes': 'Регистрация должна завершаться не позже начала соревнования.'})
        if self.age_min is not None and self.age_max is not None and self.age_max < self.age_min:
            raise ValidationError({'age_max': 'Верхняя граница возраста меньше нижней.'})

    def __str__(self):
        return self.title


class Application(models.Model):
    STATUSES = [('pending', 'На рассмотрении'), ('approved', 'Одобрена'), ('rejected', 'Отклонена')]
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name='applications')
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='applications')
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, related_name='applications')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='submitted_applications')
    status = models.CharField('Статус', max_length=12, choices=STATUSES, default='pending')
    comment = models.TextField('Комментарий', blank=True)
    created_at = models.DateTimeField('Подана', auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Заявка на соревнование'
        verbose_name_plural = 'Заявки на соревнования'
        constraints = [models.UniqueConstraint(fields=['competition', 'athlete', 'discipline'], name='unique_competition_application')]

    def clean(self):
        super().clean()
        if self.athlete_id and Role.ATHLETE not in self.athlete.roles:
            raise ValidationError({'athlete': 'Участник должен иметь роль спортсмена.'})
        if self.competition_id and self.discipline_id and not self.competition.disciplines.filter(pk=self.discipline_id).exists():
            raise ValidationError({'discipline': 'Дисциплина не входит в соревнование.'})
        if self.athlete_id and self.competition_id:
            age = self.athlete.age_on(self.competition.starts_on)
            if age is None or not self.competition.age_min <= age <= self.competition.age_max:
                raise ValidationError({'athlete': 'Спортсмен не соответствует возрастной группе.'})

    def __str__(self):
        return f'{self.athlete} · {self.competition}'


class Result(models.Model):
    application = models.OneToOneField(Application, on_delete=models.PROTECT, related_name='result')
    place = models.PositiveIntegerField('Место')
    score = models.DecimalField('Баллы', max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ['place', 'id']
        verbose_name = 'Результат'
        verbose_name_plural = 'Результаты'
        constraints = [models.CheckConstraint(condition=models.Q(place__gte=1), name='result_positive_place')]

    def clean(self):
        super().clean()
        if self.application_id:
            if self.application.status != 'approved':
                raise ValidationError({'application': 'Результат разрешён только для одобренной заявки.'})
            duplicate = Result.objects.filter(application__competition_id=self.application.competition_id, application__discipline_id=self.application.discipline_id, place=self.place).exclude(pk=self.pk)
            if duplicate.exists():
                raise ValidationError({'place': 'Это место в данной дисциплине уже занято.'})

    def __str__(self):
        return f'{self.application.athlete}: {self.place} место'


class RankApplication(models.Model):
    athlete = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='rank_applications')
    result = models.ForeignKey(Result, on_delete=models.PROTECT, null=True, blank=True, related_name='rank_applications')
    kind = models.CharField('Тип', max_length=12, choices=[('rank', 'Спортивный разряд'), ('category', 'Судейская категория')], default='rank')
    requested_title = models.CharField('Запрошено', max_length=100)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='submitted_rank_applications')
    status = models.CharField('Статус', max_length=12, choices=Application.STATUSES, default='pending')
    review_comment = models.TextField('Комментарий', blank=True)
    assigned_on = models.DateField('Дата присвоения', null=True, blank=True)
    valid_until = models.DateField('Действует до', null=True, blank=True)
    order_number = models.CharField('Номер приказа', max_length=100, blank=True)
    created_at = models.DateTimeField('Подана', auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Заявка на разряд или категорию'
        verbose_name_plural = 'Заявки на разряды и категории'
        constraints = [models.CheckConstraint(condition=models.Q(valid_until__isnull=True) | models.Q(assigned_on__isnull=True) | models.Q(valid_until__gte=models.F('assigned_on')), name='rank_validity_order')]

    def clean(self):
        super().clean()
        if self.kind == 'category' and self.athlete_id and Role.JUDGE not in self.athlete.roles:
            raise ValidationError({'athlete': 'Для судейской категории требуется роль судьи.'})
        if self.kind == 'rank':
            if not self.result_id:
                raise ValidationError({'result': 'Для разряда требуется результат соревнования.'})
            if self.result.application.athlete_id != self.athlete_id:
                raise ValidationError({'result': 'Результат принадлежит другому спортсмену.'})
        if self.valid_until and self.assigned_on and self.valid_until < self.assigned_on:
            raise ValidationError({'valid_until': 'Срок действия должен быть не раньше присвоения.'})
        if self.status == 'approved' and not (self.assigned_on and self.valid_until and self.order_number):
            raise ValidationError('Для присвоения нужны дата, срок действия и номер приказа.')

    def __str__(self):
        return f'{self.athlete}: {self.requested_title}'


class CoachLink(models.Model):
    coach = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='coaching_links')
    athlete = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='coach_links')
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, related_name='coach_links')
    status = models.CharField('Статус', max_length=12, choices=[('pending', 'Ожидает подтверждения'), ('active', 'Активна'), ('suspended', 'Приостановлена'), ('finished', 'Завершена')], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Связь тренера и спортсмена'
        verbose_name_plural = 'Связи тренеров и спортсменов'
        constraints = [models.UniqueConstraint(fields=['coach', 'athlete', 'discipline'], name='unique_coach_athlete_discipline')]

    def clean(self):
        super().clean()
        if self.coach_id and Role.COACH not in self.coach.roles:
            raise ValidationError({'coach': 'Выбранный пользователь не является тренером.'})
        if self.athlete_id and Role.ATHLETE not in self.athlete.roles:
            raise ValidationError({'athlete': 'Выбранный пользователь не является спортсменом.'})
        if self.coach_id and self.coach_id == self.athlete_id:
            raise ValidationError('Нельзя привязать тренера к самому себе.')

    def __str__(self):
        return f'{self.coach} → {self.athlete} ({self.discipline})'


class JudgeAssignment(models.Model):
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name='judge_assignments')
    judge = models.ForeignKey(Profile, on_delete=models.PROTECT, related_name='judge_assignments')
    position = models.CharField('Должность', max_length=100, default='Судья')

    class Meta:
        ordering = ['id']
        verbose_name = 'Судейское назначение'
        verbose_name_plural = 'Судейские назначения'
        constraints = [models.UniqueConstraint(fields=['competition', 'judge'], name='unique_competition_judge')]

    def clean(self):
        super().clean()
        if self.judge_id and Role.JUDGE not in self.judge.roles:
            raise ValidationError({'judge': 'Для назначения требуется роль судьи.'})

    def __str__(self):
        return f'{self.judge} · {self.position}'


class Team(models.Model):
    season = models.PositiveIntegerField('Сезон', default=current_year)
    discipline = models.ForeignKey(Discipline, on_delete=models.PROTECT, related_name='teams')
    age_min = models.PositiveIntegerField('Возраст от', default=12)
    age_max = models.PositiveIntegerField('Возраст до', default=99)
    members = models.ManyToManyField(Profile, related_name='teams', verbose_name='Спортсмены')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_teams')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-season', '-created_at']
        verbose_name = 'Сборная'
        verbose_name_plural = 'Сборные'
        constraints = [models.CheckConstraint(condition=models.Q(age_max__gte=models.F('age_min')), name='team_age_order')]

    def __str__(self):
        return f'Сборная {self.season} · {self.discipline}'


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField('Заголовок', max_length=240)
    body = models.TextField('Сообщение')
    url = models.CharField('Ссылка', max_length=400, blank=True)
    read = models.BooleanField('Прочитано', default=False)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    email_sent = models.BooleanField('Email отправлен', default=False)
    email_delivery_status = models.CharField('Доставка', max_length=12, default='queued', choices=[
        ('queued', 'В очереди'), ('processing', 'Отправляется'), ('local', 'Локальная почта'),
        ('sent', 'Принято SMTP'), ('failed', 'Ошибка'), ('skipped', 'Нет адреса'),
    ])
    email_attempts = models.PositiveIntegerField('Попытки', default=0)
    email_last_error = models.CharField('Последняя ошибка', max_length=240, blank=True)
    email_next_retry_at = models.DateTimeField('Следующая попытка', null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        indexes = [models.Index(fields=['user', 'read'])]

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    role = models.CharField('Роль', max_length=20, choices=Role.choices)
    action = models.CharField('Действие', max_length=200)
    entity = models.CharField('Объект', max_length=160, blank=True)
    detail = models.TextField('Подробности', blank=True)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Действие'
        verbose_name_plural = 'Журнал действий'

    def __str__(self):
        return f'{self.user} · {self.action}'


class RatingRule(models.Model):
    level = models.CharField('Уровень', max_length=25, choices=Competition.LEVELS, unique=True)
    first = models.PositiveIntegerField('1 место')
    second = models.PositiveIntegerField('2 место')
    third = models.PositiveIntegerField('3 место')

    class Meta:
        ordering = ['id']
        verbose_name = 'Правило рейтинга'
        verbose_name_plural = 'Правила рейтинга'

    def __str__(self):
        return f'{self.get_level_display()}: {self.first}/{self.second}/{self.third}'


class DictionaryEntry(models.Model):
    KINDS = [('competition_type', 'Тип соревнования'), ('age_group', 'Возрастная группа'),
             ('rank', 'Разряд'), ('category', 'Судейская категория'),
             ('stage', 'Этап подготовки'), ('municipality', 'Муниципалитет')]
    kind = models.CharField('Справочник', max_length=30, choices=KINDS)
    code = models.SlugField('Код', max_length=80)
    label = models.CharField('Название', max_length=200)
    metadata = models.JSONField('Параметры', default=dict, blank=True)
    active = models.BooleanField('Действует', default=True)

    class Meta:
        ordering = ['kind', 'label']
        constraints = [models.UniqueConstraint(fields=['kind', 'code'], name='unique_dictionary_code')]

    def __str__(self):
        return self.label


class DocumentLayout(models.Model):
    kind = models.CharField('Вид документа', max_length=30, unique=True, choices=[
        ('protocol', 'Протокол'), ('team', 'Состав сборной и приказ'), ('athlete', 'История спортсмена'),
        ('census', 'Численность'), ('consent', 'Согласие'), ('judging-panel', 'Судейская коллегия'), ('judging-history', 'История судейства'),
    ])
    heading = models.CharField('Шапка учреждения', max_length=240, blank=True)
    title_prefix = models.CharField('Префикс заголовка', max_length=100, blank=True)
    footer = models.CharField('Подпись / примечание', max_length=500, blank=True)

    def __str__(self):
        return self.get_kind_display()
