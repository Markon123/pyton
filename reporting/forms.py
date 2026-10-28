import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from federation.access import role_profiles
from federation.models import Competition, Discipline, Organization, Profile, Team
from .models import FORMATS, REPORT_KINDS


PARAMETER_KEYS = {'year_from', 'year_to', 'discipline', 'organization', 'age_min', 'age_max', 'municipality'}
OBJECT_PARAMETER_KEYS = {'kind', 'object_id'}
DOCUMENT_KINDS = dict(REPORT_KINDS[1:])


class CensusReportForm(forms.Form):
    year_from = forms.IntegerField(label='С года', min_value=1900)
    year_to = forms.IntegerField(label='По год', min_value=1900)
    discipline = forms.ModelChoiceField(label='Дисциплина', queryset=Discipline.objects.all(), required=False, empty_label='Все дисциплины')
    organization = forms.ModelChoiceField(label='Организация', queryset=Organization.objects.all(), required=False, empty_label='Все организации')
    age_min = forms.IntegerField(label='Возраст от', min_value=0, max_value=120, required=False)
    age_max = forms.IntegerField(label='Возраст до', min_value=0, max_value=120, required=False)
    municipality = forms.CharField(label='Муниципалитет', max_length=120, required=False)
    format = forms.ChoiceField(label='Формат', choices=FORMATS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        year = timezone.localdate().year
        self.fields['year_from'].max_value = year
        self.fields['year_to'].max_value = year
        self.fields['year_from'].initial = year - 1
        self.fields['year_to'].initial = year
        self.fields['format'].initial = 'xlsx'

    def clean(self):
        data = super().clean()
        start, end = data.get('year_from'), data.get('year_to')
        current = timezone.localdate().year
        if start is not None and end is not None:
            if start > end:
                self.add_error('year_to', 'Окончание периода должно быть не раньше начала.')
            if start > current or end > current:
                self.add_error('year_to', 'Отчётный год не может быть в будущем.')
            if end - start > 100:
                self.add_error('year_from', 'Один отчёт охватывает не более 101 года.')
        if data.get('age_min') is not None and data.get('age_max') is not None and data['age_min'] > data['age_max']:
            self.add_error('age_max', 'Верхняя граница возраста меньше нижней.')
        return data

    def parameters(self):
        if not self.is_valid():
            raise ValidationError('Параметры отчёта не прошли проверку.')
        return {key: (self.cleaned_data[key].pk if key in ('discipline', 'organization') and self.cleaned_data[key]
                      else self.cleaned_data.get(key) or None if key == 'municipality'
                      else self.cleaned_data.get(key)) for key in PARAMETER_KEYS}


class ReportObjectChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        if value not in self.empty_values:
            key = value.pk if hasattr(value, 'pk') else value
            if type(key) is not int and not (isinstance(key, str) and re.fullmatch(r'[1-9][0-9]*', key)):
                raise ValidationError('Выберите запись из списка.')
            if int(key) <= 0:
                raise ValidationError('Выберите запись из списка.')
        return super().to_python(value)

    def label_from_instance(self, obj):
        if isinstance(obj, Competition):
            return f'{obj.title} · {obj.starts_on:%d.%m.%Y}'
        if isinstance(obj, Profile):
            return f'{obj.display_name} · {obj.organization or "Без организации"}'
        return str(obj)


class DocumentReportForm(forms.Form):
    kind = forms.ChoiceField(choices=REPORT_KINDS[1:], widget=forms.HiddenInput)
    object_id = ReportObjectChoiceField(label='Документ', queryset=Competition.objects.none())
    format = forms.ChoiceField(label='Формат', choices=FORMATS, initial='xlsx')

    def __init__(self, *args, kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        selected = kind or (self.data.get('kind') if self.is_bound else self.initial.get('kind'))
        self.fields['kind'].initial = selected
        if selected == 'protocol':
            objects = Competition.objects.filter(Q(protocol_published=True) | Q(state='closed'))
            label = 'Соревнование'
        elif selected == 'athlete':
            objects, label = role_profiles('athlete'), 'Спортсмен'
        elif selected == 'team':
            objects, label = Team.objects.select_related('discipline'), 'Сборная'
        elif selected == 'judging-panel':
            objects, label = Competition.objects.all(), 'Соревнование'
        elif selected == 'judging-history':
            objects, label = role_profiles('judge'), 'Судья'
        else:
            objects, label = Competition.objects.none(), 'Документ'
        self.fields['object_id'].queryset = objects
        self.fields['object_id'].label = label

    def clean(self):
        data = super().clean()
        obj = data.get('object_id')
        if data.get('kind') == 'protocol' and obj and not obj.protocol_published:
            approved = obj.applications.filter(status='approved')
            if obj.state != 'closed' or obj.applications.filter(status='pending').exists() or not approved.exists() or approved.filter(result__isnull=True).exists():
                self.add_error('object_id', 'Для предварительного протокола закройте регистрацию, рассмотрите все заявки и внесите результаты одобренных участников.')
        return data

    def parameters(self):
        if not self.is_valid():
            raise ValidationError('Параметры отчёта не прошли проверку.')
        return {'kind': self.cleaned_data['kind'], 'object_id': self.cleaned_data['object_id'].pk}


def validate_saved_params(params, fmt):
    if not isinstance(params, dict):
        raise ValidationError('Неподдерживаемые параметры отчёта.')
    if set(params) == OBJECT_PARAMETER_KEYS:
        if not isinstance(params['kind'], str) or params['kind'] not in DOCUMENT_KINDS or type(params['object_id']) is not int or params['object_id'] <= 0:
            raise ValidationError('Неподдерживаемый вид документа или запись.')
        form = DocumentReportForm({**params, 'format': fmt}, kind=params['kind'])
        if not form.is_valid():
            raise ValidationError('Параметры фонового документа недействительны.')
        return form
    if set(params) != PARAMETER_KEYS:
        raise ValidationError('Неподдерживаемые параметры отчёта.')
    for key in PARAMETER_KEYS - {'municipality'}:
        value = params[key]
        if value is not None and type(value) is not int:
            raise ValidationError('Числовые параметры должны быть целыми числами.')
    if params['municipality'] is not None and not isinstance(params['municipality'], str):
        raise ValidationError('Муниципалитет должен быть строкой.')
    form = CensusReportForm({**params, 'format': fmt})
    if not form.is_valid():
        raise ValidationError('Параметры фонового отчёта недействительны.')
    return form
