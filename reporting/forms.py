from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from federation.models import Discipline, Organization
from .models import FORMATS


PARAMETER_KEYS = {'year_from', 'year_to', 'discipline', 'organization', 'age_min', 'age_max', 'municipality'}


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


def validate_saved_params(params, fmt):
    if not isinstance(params, dict) or set(params) != PARAMETER_KEYS:
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
