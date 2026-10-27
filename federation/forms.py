"""Forms are the validation boundary for the role-scoped HTML workflows."""

from datetime import date
from pathlib import Path

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.utils import timezone

from .models import (
    Application, CoachLink, Competition, Consent, Discipline, JudgeAssignment,
    Organization, Profile, RankApplication, Result, Role,
)


User = get_user_model()

RANK_TITLES = (
    'III юношеский', 'II юношеский', 'I юношеский',
    'III разряд', 'II разряд', 'I разряд', 'КМС', 'МС', 'МСМК',
)
CATEGORY_TITLES = (
    'Юный спортивный судья', 'Судья третьей категории',
    'Судья второй категории', 'Судья первой категории',
    'Всероссийская категория',
)


def configured_titles(kind, fallback):
    from .models import DictionaryEntry
    entries = list(DictionaryEntry.objects.filter(kind=kind, active=True).order_by('id'))
    return [item.label for item in sorted(entries, key=lambda item: item.metadata.get('order') if isinstance(item.metadata.get('order'), int) else item.pk)] if entries else list(fallback)


def date_widget():
    return forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})


def with_role(role, queryset=None):
    """SQLite does not support JSON contains; never loosen role filtering."""
    queryset = Profile.objects.all() if queryset is None else queryset
    ids = [pk for pk, roles in queryset.values_list('pk', 'roles') if role in roles]
    return queryset.filter(pk__in=ids).select_related('user', 'organization')


def validate_birth_date(value):
    if value is None:
        return
    if value < date(1900, 1, 1) or value > timezone.localdate():
        raise forms.ValidationError('Укажите настоящую дату рождения с 1900 года по сегодняшний день.')


def is_minor(born):
    today = timezone.localdate()
    return born and today.year - born.year - ((today.month, today.day) < (born.month, born.day)) < 18


def validate_years(form, data):
    year = data.get('start_year')
    birth = data.get('birth_date')
    if year is not None and (year < 1900 or year > timezone.localdate().year):
        form.add_error('start_year', 'Год начала занятий должен быть от 1900 до текущего года.')
    if year is not None and birth and year < birth.year:
        form.add_error('start_year', 'Начало занятий не может быть раньше года рождения.')
    end = data.get('end_year')
    if end is not None and year is not None and end < year:
        form.add_error('end_year', 'Год окончания не может быть раньше года начала занятий.')
    if end is not None and end > timezone.localdate().year:
        form.add_error('end_year', 'Год окончания не может быть в будущем.')


class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(label='Имя', max_length=150)
    last_name = forms.CharField(label='Фамилия', max_length=150)
    email = forms.EmailField(label='Электронная почта')
    roles = forms.MultipleChoiceField(
        label='Ваши роли', widget=forms.CheckboxSelectMultiple,
        choices=[choice for choice in Role.choices if choice[0] in ('athlete', 'coach', 'judge')],
    )
    birth_date = forms.DateField(label='Дата рождения', widget=date_widget(), validators=[validate_birth_date])
    municipality = forms.CharField(label='Муниципалитет', max_length=120)
    organization = forms.ModelChoiceField(label='Организация', queryset=Organization.objects.all(), required=False, empty_label='Без организации')
    disciplines = forms.ModelMultipleChoiceField(label='Дисциплины', queryset=Discipline.objects.all(), widget=forms.CheckboxSelectMultiple)
    start_year = forms.IntegerField(label='Год начала занятий', min_value=1900)
    personal_consent = forms.BooleanField(label='Даю согласие на обработку персональных данных для учёта участия в спортивном программировании (версия 1.0).')
    guardian_name = forms.CharField(label='ФИО законного представителя (до 18 лет)', max_length=200, required=False)
    guardian_consent = forms.BooleanField(label='Законный представитель согласен на обработку персональных данных несовершеннолетнего (версия 1.0).', required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def clean(self):
        data = super().clean()
        validate_years(self, data)
        if is_minor(data.get('birth_date')):
            if not data.get('guardian_name'):
                self.add_error('guardian_name', 'Для несовершеннолетнего требуется ФИО законного представителя.')
            if not data.get('guardian_consent'):
                self.add_error('guardian_consent', 'Для несовершеннолетнего требуется согласие законного представителя.')
        return data

    @transaction.atomic
    def save(self, commit=True):
        if not commit:
            raise ValueError('RegistrationForm.save requires commit=True to retain the profile and consents atomically.')
        user = super().save(commit=True)
        person = Profile.objects.create(
            user=user, roles=self.cleaned_data['roles'],
            birth_date=self.cleaned_data['birth_date'],
            municipality=self.cleaned_data['municipality'],
            organization=self.cleaned_data.get('organization'),
            start_year=self.cleaned_data['start_year'],
            guardian_name=self.cleaned_data.get('guardian_name', ''),
        )
        person.disciplines.set(self.cleaned_data['disciplines'])
        Consent.objects.create(user=user, kind='personal_data')
        if self.cleaned_data.get('guardian_consent'):
            Consent.objects.create(user=user, kind='guardian', representative_name=person.guardian_name)
        return user


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(label='Имя', max_length=150)
    last_name = forms.CharField(label='Фамилия', max_length=150)
    birth_date = forms.DateField(label='Дата рождения', widget=date_widget(), validators=[validate_birth_date])
    guardian_consent = forms.BooleanField(
        label='Законный представитель подтверждает согласие на обработку данных несовершеннолетнего (версия 1.0).', required=False,
    )

    class Meta:
        model = Profile
        fields = (
            'first_name', 'last_name', 'birth_date', 'municipality', 'organization',
            'disciplines', 'start_year', 'guardian_name', 'guardian_consent',
            'anti_doping_date', 'anti_doping_url',
        )
        widgets = {
            'disciplines': forms.CheckboxSelectMultiple,
            'anti_doping_date': date_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['disciplines'].required = True
        self.fields['municipality'].required = True
        self.fields['organization'].empty_label = 'Без организации'
        if self.instance and self.instance.user_id:
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name

    def clean(self):
        data = super().clean()
        validate_years(self, data)
        if is_minor(data.get('birth_date')):
            guardian = data.get('guardian_name')
            if not guardian:
                self.add_error('guardian_name', 'Укажите законного представителя несовершеннолетнего.')
            existing_consent = bool(
                self.instance.user_id and guardian and
                Consent.objects.filter(user_id=self.instance.user_id, kind='guardian', representative_name=guardian).exists()
            )
            if not existing_consent and not data.get('guardian_consent'):
                self.add_error('guardian_consent', 'Подтвердите согласие законного представителя.')
        course_date = data.get('anti_doping_date')
        if course_date and course_date > timezone.localdate():
            self.add_error('anti_doping_date', 'Дата прохождения курса не может быть в будущем.')
        return data

    @transaction.atomic
    def save(self, commit=True):
        person = super().save(commit=False)
        if commit:
            person.user.first_name = self.cleaned_data['first_name']
            person.user.last_name = self.cleaned_data['last_name']
            person.user.save(update_fields=['first_name', 'last_name'])
            person.save()
            self.save_m2m()
            if self.cleaned_data.get('guardian_consent') and self.cleaned_data.get('guardian_name'):
                Consent.objects.get_or_create(
                    user=person.user, kind='guardian',
                    representative_name=self.cleaned_data['guardian_name'], version='1.0',
                )
        return person


class FederationProfileForm(ProfileForm):
    roles = forms.MultipleChoiceField(label='Роли', choices=Role.choices, widget=forms.CheckboxSelectMultiple)

    class Meta(ProfileForm.Meta):
        fields = ProfileForm.Meta.fields + ('roles', 'status', 'end_year', 'disqualified_until')
        widgets = {**ProfileForm.Meta.widgets, 'disqualified_until': date_widget()}

    def clean(self):
        data = super().clean()
        if data.get('status') == 'archived' and not data.get('end_year'):
            self.add_error('end_year', 'Для архивной записи укажите год окончания занятий: он используется в отчёте о численности.')
        return data


class CompetitionForm(forms.ModelForm):
    class Meta:
        model = Competition
        exclude = ('protocol_published',)
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'disciplines': forms.CheckboxSelectMultiple,
            **{field: date_widget() for field in ('starts_on', 'ends_on', 'registration_opens', 'registration_closes')},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['state'].choices = [choice for choice in Competition.STATES if choice[0] != 'finished']
        from .models import DictionaryEntry
        types = list(DictionaryEntry.objects.filter(kind='competition_type', active=True).values_list('label', 'label'))
        if types:
            self.fields['kind'] = forms.ChoiceField(label='Тип', choices=types)
        self.fields['regulation'].help_text = 'PDF или DOCX, до 5 МБ. Для открытия регистрации необходимо положение.'

    def clean_regulation(self):
        uploaded = self.cleaned_data.get('regulation')
        if uploaded:
            if uploaded.size > 5 * 1024 * 1024:
                raise forms.ValidationError('Размер положения не должен превышать 5 МБ.')
            if Path(uploaded.name).suffix.lower() not in ('.pdf', '.docx'):
                raise forms.ValidationError('Положение должно быть в формате PDF или DOCX.')
        return uploaded

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.protocol_published:
            raise forms.ValidationError('Опубликованное соревнование закрыто для редактирования. Сначала отзовите публикацию протокола.')
        if data.get('state') in ('published', 'open') and not data.get('regulation'):
            self.add_error('regulation', 'Перед открытием регистрации загрузите положение.')
        if data.get('age_max', 0) > 120:
            self.add_error('age_max', 'Укажите возраст не более 120 лет.')
        return data


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ('athlete', 'discipline')
        labels = {'athlete': 'Спортсмен', 'discipline': 'Дисциплина'}

    def __init__(self, *args, user, competition, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.user, self.competition, self.role = user, competition, role
        self.instance.competition = competition
        self.instance.submitted_by = user
        candidates = Profile.objects.filter(status='active')
        if role == Role.ATHLETE:
            candidates = candidates.filter(user=user)
        elif role == Role.COACH:
            candidates = candidates.filter(coach_links__coach__user=user, coach_links__status='active').distinct()
        else:
            candidates = candidates.none()
        self.fields['athlete'].queryset = with_role(Role.ATHLETE, candidates)
        self.fields['discipline'].queryset = competition.disciplines.all()
        if role == Role.ATHLETE:
            self.fields['athlete'].initial = candidates.first()

    def clean(self):
        data = super().clean()
        if self.role not in (Role.ATHLETE, Role.COACH):
            raise forms.ValidationError('Подавать заявки может спортсмен или его действующий тренер.')
        if not self.competition.is_registration_open:
            raise forms.ValidationError('Приём заявок закрыт или срок регистрации ещё не наступил.')
        athlete, discipline = data.get('athlete'), data.get('discipline')
        if athlete:
            from governance.services import active_disqualifications
            if active_disqualifications().filter(athlete=athlete).exists():
                self.add_error('athlete', 'Действует решение об отстранении спортсмена.')
            if athlete.disqualified_until and athlete.disqualified_until >= timezone.localdate():
                self.add_error('athlete', 'Спортсмен отстранён от участия в соревнованиях.')
            age = athlete.age_on(self.competition.starts_on)
            if age is None or not self.competition.age_min <= age <= self.competition.age_max:
                self.add_error('athlete', 'Возраст спортсмена на дату соревнования не соответствует условиям.')
        if athlete and discipline:
            if not athlete.disciplines.filter(pk=discipline.pk).exists():
                self.add_error('discipline', 'Эта дисциплина не указана в профиле спортсмена.')
            if self.role == Role.COACH and not CoachLink.objects.filter(coach__user=self.user, athlete=athlete, discipline=discipline, status='active').exists():
                self.add_error('discipline', 'Нет подтверждённой связи с этим спортсменом по выбранной дисциплине.')
            duplicate = Application.objects.filter(competition=self.competition, athlete=athlete, discipline=discipline)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error('athlete', 'Заявка этого спортсмена по выбранной дисциплине уже подана.')
        return data


class ResultForm(forms.ModelForm):
    class Meta:
        model = Result
        fields = ('application', 'place', 'score')
        labels = {'application': 'Одобренная заявка'}

    def __init__(self, *args, competition, **kwargs):
        super().__init__(*args, **kwargs)
        self.competition = competition
        applications = Application.objects.filter(competition=competition, status='approved').select_related('athlete__user', 'discipline', 'competition')
        self.fields['application'].queryset = applications
        self.fields['place'].min_value = 1
        self.fields['score'].min_value = 0
        # The same form adds or corrects a result. Bind the existing instance
        # before ModelForm invokes Result.clean() and one-to-one validation.
        if self.is_bound and not self.instance.pk:
            raw_id = self.data.get(self.add_prefix('application'))
            try:
                application = applications.filter(pk=raw_id).first()
            except (ValueError, TypeError):
                application = None
            if application:
                existing = Result.objects.filter(application=application).first()
                if existing:
                    self.instance = existing

    def clean(self):
        data = super().clean()
        if self.competition.protocol_published:
            raise forms.ValidationError('Опубликованный протокол закрыт для изменения результатов.')
        if data.get('score') is not None and data['score'] < 0:
            self.add_error('score', 'Баллы не могут быть отрицательными.')
        if data.get('place') is not None and data['place'] < 1:
            self.add_error('place', 'Место должно быть положительным числом.')
        return data


class JudgeAssignmentForm(forms.ModelForm):
    class Meta:
        model = JudgeAssignment
        fields = ('judge', 'position')
        labels = {'judge': 'Судья'}

    def __init__(self, *args, competition, **kwargs):
        super().__init__(*args, **kwargs)
        self.competition = competition
        self.instance.competition = competition
        self.fields['judge'].queryset = with_role(Role.JUDGE, Profile.objects.filter(status='active'))
        # Existing assignments are intentionally updatable through the form.
        raw_id = self.data.get(self.add_prefix('judge')) if self.is_bound else None
        if raw_id:
            try:
                existing = JudgeAssignment.objects.filter(competition=competition, judge_id=raw_id).first()
            except (ValueError, TypeError):
                existing = None
            if existing:
                self.instance = existing

    def clean(self):
        data = super().clean()
        if self.competition.protocol_published:
            raise forms.ValidationError('Судейская коллегия опубликованного протокола закрыта для изменений.')
        return data


class QualificationForm(forms.Form):
    kind = forms.ChoiceField(label='Вид заявки', choices=RankApplication._meta.get_field('kind').choices)
    athlete = forms.ModelChoiceField(label='Спортсмен / судья', queryset=Profile.objects.none())
    result = forms.ModelChoiceField(label='Результат в опубликованном протоколе', queryset=Result.objects.none(), required=False)
    requested_title = forms.ChoiceField(
        label='Запрашиваемый разряд / категория',
        choices=[('Разряды', [(v, v) for v in RANK_TITLES]), ('Судейские категории', [(v, v) for v in CATEGORY_TITLES])],
    )

    def __init__(self, *args, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.rank_titles = configured_titles('rank', RANK_TITLES)
        self.category_titles = configured_titles('category', CATEGORY_TITLES)
        self.fields['requested_title'].choices = [('Разряды', [(v, v) for v in self.rank_titles]), ('Судейские категории', [(v, v) for v in self.category_titles])]
        self.user, self.role = user, role
        candidates = Profile.objects.filter(status='active')
        if role in (Role.ATHLETE, Role.JUDGE):
            candidates = candidates.filter(user=user)
        elif role == Role.COACH:
            candidates = candidates.filter(coach_links__coach__user=user, coach_links__status='active').distinct()
        elif role != Role.FEDERATION:
            candidates = candidates.none()
        self.fields['athlete'].queryset = candidates.select_related('user')
        self.fields['result'].queryset = Result.objects.filter(
            application__athlete__in=candidates, application__status='approved',
            application__competition__protocol_published=True,
        ).select_related('application__athlete__user', 'application__competition', 'application__discipline')
        if role == Role.JUDGE:
            self.fields['kind'].choices = [('category', 'Судейская категория')]
            self.fields['kind'].initial = 'category'
        elif role in (Role.ATHLETE, Role.COACH):
            self.fields['kind'].choices = [('rank', 'Спортивный разряд')]
        if role in (Role.ATHLETE, Role.JUDGE):
            self.fields['athlete'].initial = candidates.first()

    def clean(self):
        data = super().clean()
        kind, athlete, result = data.get('kind'), data.get('athlete'), data.get('result')
        title = data.get('requested_title')
        if kind == 'category':
            if self.role not in (Role.JUDGE, Role.FEDERATION):
                self.add_error('kind', 'На судейскую категорию заявка подаётся из кабинета судьи.')
            if athlete and Role.JUDGE not in athlete.roles:
                self.add_error('athlete', 'Для получения категории требуется роль судьи.')
            if result:
                self.add_error('result', 'В заявке на категорию результат спортсмена не используется.')
            if title and title not in self.category_titles:
                self.add_error('requested_title', 'Для судьи выберите судейскую категорию.')
        elif kind == 'rank':
            if self.role not in (Role.ATHLETE, Role.COACH, Role.FEDERATION):
                self.add_error('kind', 'На разряд заявка подаётся из кабинета спортсмена или тренера.')
            if athlete and Role.ATHLETE not in athlete.roles:
                self.add_error('athlete', 'Для получения разряда требуется роль спортсмена.')
            if not result:
                self.add_error('result', 'Выберите результат в опубликованном протоколе.')
            elif athlete:
                if result.application.athlete_id != athlete.pk:
                    self.add_error('result', 'Этот результат принадлежит другому спортсмену.')
                links = CoachLink.objects.filter(athlete=athlete, discipline=result.application.discipline, status='active')
                if self.role == Role.ATHLETE and links.exists():
                    self.add_error('athlete', 'У вас есть действующий тренер по этой дисциплине. Заявку на разряд подаёт тренер.')
                if self.role == Role.COACH and not links.filter(coach__user=self.user).exists():
                    self.add_error('result', 'Нет подтверждённой тренерской связи по дисциплине этого результата.')
            if title and title not in self.rank_titles:
                self.add_error('requested_title', 'Для спортсмена выберите спортивный разряд.')
        if athlete and kind and title:
            duplicate = RankApplication.objects.filter(athlete=athlete, kind=kind, requested_title=title, status='pending')
            if kind == 'rank' and result:
                duplicate = duplicate.filter(result=result)
            if duplicate.exists():
                raise forms.ValidationError('Такая заявка уже находится на рассмотрении.')
        return data


class CoachLinkForm(forms.ModelForm):
    class Meta:
        model = CoachLink
        fields = ('athlete', 'discipline')
        labels = {'athlete': 'Спортсмен', 'discipline': 'Дисциплина'}

    def __init__(self, *args, coach=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.coach = coach
        candidates = with_role(Role.ATHLETE, Profile.objects.filter(status='active'))
        if coach:
            candidates = candidates.exclude(pk=coach.pk)
            self.instance.coach = coach
            self.fields['discipline'].queryset = coach.disciplines.all()
        self.fields['athlete'].queryset = candidates

    def clean(self):
        data = super().clean()
        athlete, discipline = data.get('athlete'), data.get('discipline')
        if self.coach is None or Role.COACH not in self.coach.roles:
            raise forms.ValidationError('Запрос на привязку может отправить тренер.')
        if athlete and self.coach.pk == athlete.pk:
            self.add_error('athlete', 'Нельзя привязать тренера к самому себе.')
        if athlete and discipline:
            if not athlete.disciplines.filter(pk=discipline.pk).exists():
                self.add_error('discipline', 'Спортсмен не занимается выбранной дисциплиной.')
            if CoachLink.objects.filter(coach=self.coach, athlete=athlete, discipline=discipline).exists():
                raise forms.ValidationError('Связь по этой дисциплине уже создана. Измените её статус в списке.')
        return data


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ('name', 'kind', 'municipality')
