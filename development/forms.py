from pathlib import Path
from django import forms
from django.utils import timezone
from federation.models import Competition, Result
from federation.forms import RANK_TITLES, configured_titles
from .models import Evidence, RankCriterion


class CriterionForm(forms.ModelForm):
    rank_title = forms.ChoiceField(label='Следующий разряд')
    levels = forms.MultipleChoiceField(label='Уровни соревнований', choices=Competition.LEVELS, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = RankCriterion
        fields = ['discipline', 'rank_title', 'levels', 'max_place', 'min_score', 'age_min', 'age_max',
                  'source_text', 'source_url', 'active', 'is_demo']
        widgets = {'source_text': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        titles = configured_titles('rank', RANK_TITLES)
        # Preserve an already stored title for editing when its dictionary entry
        # was retired; new values must still come from the active dictionary.
        if self.instance.pk and self.instance.rank_title not in titles:
            titles.append(self.instance.rank_title)
        self.fields['rank_title'].choices = [(value, value) for value in titles]


class EvidenceForm(forms.ModelForm):
    class Meta:
        model = Evidence
        fields = ['year', 'indicator', 'citation', 'document', 'url']
        widgets = {'citation': forms.Textarea(attrs={'rows': 3})}

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if file and hasattr(file, 'read'):
            if file.size > 5 * 1024 * 1024:
                raise forms.ValidationError('Документ должен быть не более 5 МБ.')
            extension = Path(file.name).suffix.lower()
            head = file.read(16)
            file.seek(0)
            signatures = {'.pdf': head.startswith(b'%PDF-'), '.png': head.startswith(b'\x89PNG\r\n\x1a\n'),
                          '.jpg': head.startswith(b'\xff\xd8\xff'), '.jpeg': head.startswith(b'\xff\xd8\xff')}
            if not signatures.get(extension, False):
                raise forms.ValidationError('Принимаются настоящие PDF, PNG и JPEG.')
        return file


class CertificateForm(forms.Form):
    result = forms.ModelChoiceField(label='Результат в опубликованном протоколе', queryset=Result.objects.none())
    public_consent = forms.BooleanField(label='Согласен на публичную проверку номера, инициалов, соревнования и результата справки.')

    def __init__(self, *args, results, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['result'].queryset = results
