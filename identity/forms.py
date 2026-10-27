from django import forms
from oauth2_provider.models import get_application_model

from .validator import redirect_is_secure


class ClientForm(forms.ModelForm):
    class Meta:
        model = get_application_model()
        fields = ['name', 'redirect_uris']
        labels = {'name': 'Название приложения', 'redirect_uris': 'Разрешённые адреса возврата (HTTPS)'}
        widgets = {'redirect_uris': forms.Textarea(attrs={'rows': 3})}
        help_texts = {'redirect_uris': 'Точные HTTPS-адреса через пробел или с новой строки. Маски и фрагменты запрещены.'}

    def clean_redirect_uris(self):
        values = self.cleaned_data['redirect_uris'].split()
        if not values or len(values) > 10 or any(not redirect_is_secure('external-client', value) for value in values):
            raise forms.ValidationError('Укажите от 1 до 10 точных HTTPS-адресов без логина, пароля, масок и фрагментов.')
        return ' '.join(dict.fromkeys(values))
