from datetime import timedelta
from django import forms
from django.utils import timezone
from .models import EVENTS, SCOPES, PartnerToken, WebhookEndpoint


class TokenForm(forms.Form):
    name = forms.CharField(label='Название интеграции', max_length=150)
    scopes = forms.MultipleChoiceField(label='Разрешения', choices=[(v, v) for v in SCOPES], widget=forms.CheckboxSelectMultiple)
    days = forms.IntegerField(label='Действует дней', min_value=1, max_value=365, initial=30)


class EndpointForm(forms.ModelForm):
    events = forms.MultipleChoiceField(label='События', choices=[(v, v) for v in EVENTS], widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = WebhookEndpoint
        fields = ['name', 'url', 'events']


class ImportForm(forms.Form):
    from federation.models import Competition
    competition = forms.ModelChoiceField(label='Соревнование', queryset=Competition.objects.filter(state='closed', protocol_published=False))
    file = forms.FileField(label='Файл CSV или JSON')

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError('Размер файла не должен превышать 2 МБ.')
        if not file.name.lower().endswith(('.json', '.csv')):
            raise forms.ValidationError('Поддерживаются CSV и JSON.')
        return file
