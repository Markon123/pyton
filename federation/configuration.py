from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .access import roles_required
from .models import DictionaryEntry, Discipline, DocumentLayout, Organization
from .services import audit


class EntryForm(forms.ModelForm):
    class Meta:
        model = DictionaryEntry
        fields = ['kind', 'code', 'label', 'metadata', 'active']

    def clean_metadata(self):
        value = self.cleaned_data.get('metadata') or {}
        if not isinstance(value, dict):
            raise forms.ValidationError('Параметры должны быть JSON-объектом.')
        return value


class DisciplineForm(forms.ModelForm):
    class Meta:
        model = Discipline
        fields = ['name', 'code']


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['name', 'kind', 'municipality']


class LayoutForm(forms.ModelForm):
    class Meta:
        model = DocumentLayout
        fields = ['kind', 'heading', 'title_prefix', 'footer']


FORMS = {'dictionary': EntryForm, 'discipline': DisciplineForm, 'organization': OrganizationForm, 'layout': LayoutForm}


@roles_required('federation')
def configuration(request):
    kind = request.GET.get('kind', 'dictionary')
    if kind not in FORMS:
        kind = 'dictionary'
    form_class = FORMS[kind]
    obj = get_object_or_404(form_class._meta.model, pk=request.GET['edit']) if request.GET.get('edit', '').isdigit() else None
    form = form_class(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        record = form.save()
        audit(request, 'Изменён справочник / макет', f'{kind}:{record.pk}', str(record))
        messages.success(request, 'Настройки сохранены.')
        return redirect(f'{request.path}?kind={kind}')
    return render(request, 'federation/configuration.html', {'form': form, 'kind': kind,
        'records': form_class._meta.model.objects.all()}, status=400 if request.method == 'POST' else 200)
