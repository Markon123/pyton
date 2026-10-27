from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from federation.access import active_role, managed_profiles, role_profiles
from federation.models import CoachLink, Competition, Result
from .models import Appeal, AwardPetition, AwardRule, ViolationRecord


def validate_evidence(upload):
    if not upload:
        return upload
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('Доказательства должны быть не больше 5 МБ.')
    ext = Path(upload.name).suffix.lower()
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff'}
    if ext not in signatures:
        raise ValidationError('Допустимы PDF, PNG или JPEG.')
    head = upload.read(12)
    upload.seek(0)
    if not head.startswith(signatures[ext]):
        raise ValidationError('Содержимое файла не соответствует расширению.')
    return upload


class AppealForm(forms.ModelForm):
    class Meta:
        model = Appeal
        fields = ['result', 'violation', 'reason', 'evidence_url', 'evidence_file']
        labels = {'result': 'Результат соревнования', 'violation': 'Решение о нарушении'}
        widgets = {'reason': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        people = managed_profiles(request)
        results = Result.objects.filter(application__athlete__in=people, application__status='approved', application__competition__protocol_published=True).select_related('application__athlete__user', 'application__competition')
        if active_role(request) == 'coach':
            ids = [r.pk for r in results if CoachLink.objects.filter(coach__user=request.user, athlete_id=r.application.athlete_id, discipline_id=r.application.discipline_id, status='active').exists()]
            results = results.filter(pk__in=ids)
        self.fields['result'].queryset = results
        violations = ViolationRecord.objects.filter(athlete__in=people, status='confirmed').select_related('athlete__user', 'competition')
        if active_role(request) == 'coach':
            from .services import may_appeal
            violations = violations.filter(pk__in=[v.pk for v in violations if may_appeal(request, v.athlete, v.competition)])
        self.fields['violation'].queryset = violations
        self.instance.submitted_by = request.user
        self.fields['result'].required = False
        self.fields['evidence_file'].help_text = 'PDF, PNG или JPEG, максимум 5 МБ. Доступ только участникам рассмотрения.'

    def clean_evidence_file(self):
        return validate_evidence(self.cleaned_data.get('evidence_file'))

    def clean_evidence_url(self):
        url = self.cleaned_data.get('evidence_url', '')
        if url and not url.startswith('https://'):
            raise ValidationError('Используйте HTTPS-ссылку.')
        return url


class AppealReviewForm(forms.Form):
    status = forms.ChoiceField(label='Решение', choices=Appeal.STATUSES[1:])
    reason = forms.CharField(label='Мотивированное решение', widget=forms.Textarea(attrs={'rows': 4}))
    place = forms.IntegerField(label='Новое место (если меняется)', min_value=1, required=False)
    score = forms.DecimalField(label='Новые баллы (если меняются)', min_value=0, max_digits=10, decimal_places=2, required=False)


class ViolationForm(forms.ModelForm):
    class Meta:
        model = ViolationRecord
        fields = ['athlete', 'competition', 'reason', 'evidence_url', 'evidence_file', 'decision', 'starts_on', 'ends_on']
        labels = {'athlete': 'Спортсмен', 'competition': 'Соревнование'}
        widgets = {'starts_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), 'ends_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), 'reason': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['athlete'].queryset = role_profiles('athlete')
        self.fields['competition'].queryset = Competition.objects.exclude(state='draft')

    def clean_evidence_file(self):
        return validate_evidence(self.cleaned_data.get('evidence_file'))

    def clean_evidence_url(self):
        url = self.cleaned_data.get('evidence_url', '')
        if url and not url.startswith('https://'):
            raise ValidationError('Используйте HTTPS-ссылку.')
        return url


class AwardRuleForm(forms.ModelForm):
    class Meta:
        model = AwardRule
        fields = ['level', 'place', 'amount', 'source', 'demonstration', 'active']


class PetitionTransitionForm(forms.ModelForm):
    class Meta:
        model = AwardPetition
        fields = ['status', 'comment', 'passport_mask', 'inn_mask', 'snils_mask', 'account_mask']

    def clean(self):
        data = super().clean()
        transitions = {'prepared': {'prepared', 'sent'}, 'sent': {'sent', 'awarded'}, 'awarded': {'awarded'}}
        if data.get('status') not in transitions[self.instance.status]:
            self.add_error('status', 'Допустимый путь: подготовлено → направлено → премия назначена.')
        return data
