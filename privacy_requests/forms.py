from django import forms

from .models import PrivacyRequest


class PrivacyRequestForm(forms.ModelForm):
    class Meta:
        model = PrivacyRequest
        fields = ['reason']
        labels = {'reason': 'Комментарий к обращению (необязательно)'}
        widgets = {'reason': forms.Textarea(attrs={'rows': 4})}


class ReviewForm(forms.Form):
    status = forms.ChoiceField(label='Решение', choices=[])
    decision_reason = forms.CharField(
        label='Ответ заявителю', max_length=4000,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def __init__(self, *args, record, **kwargs):
        super().__init__(*args, **kwargs)
        states = [PrivacyRequest.Status.APPROVED, PrivacyRequest.Status.REJECTED]
        if record.status == PrivacyRequest.Status.REQUESTED:
            states.insert(0, PrivacyRequest.Status.REVIEWING)
        self.fields['status'].choices = [(value, value.label) for value in states]

    def clean_decision_reason(self):
        value = self.cleaned_data['decision_reason'].strip()
        if not value:
            raise forms.ValidationError('Напишите ответ заявителю.')
        return value
