from django import forms
from django.db.models import Q
from django.utils import timezone

from federation.access import role_profiles
from federation.models import CoachLink, Discipline, Organization, Profile
from .models import (AssessmentNorm, AssessmentResult, Enrollment, OrganizationProgram,
                     Session, Stage, TrainingGroup)


def date_input():
    return forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})


class ProgramForm(forms.ModelForm):
    class Meta:
        model = OrganizationProgram
        fields = ('organization', 'discipline', 'stage', 'license_details', 'description')
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class GroupForm(forms.ModelForm):
    class Meta:
        model = TrainingGroup
        fields = ('name', 'organization', 'discipline', 'stage', 'coach', 'active')

    def __init__(self, *args, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['coach'].queryset = role_profiles('coach').filter(status='active')
        if role == 'coach':
            self.fields['coach'].queryset = self.fields['coach'].queryset.filter(user=user)
            self.fields['coach'].initial = user.profile
            self.fields['discipline'].queryset = user.profile.disciplines.all()
            self.fields['organization'].queryset = Organization.objects.filter(pk=user.profile.organization_id)


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ('athlete', 'start_date', 'end_date', 'municipality', 'sex')
        widgets = {'start_date': date_input(), 'end_date': date_input()}

    def __init__(self, *args, group, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.group = group
        candidates = role_profiles('athlete').filter(status='active', disciplines=group.discipline)
        if role == 'coach':
            candidates = candidates.filter(coach_links__coach=group.coach, coach_links__discipline=group.discipline, coach_links__status='active')
        self.fields['athlete'].queryset = candidates.distinct()
        self.fields['municipality'].initial = group.organization.municipality


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ('date', 'topic', 'format', 'confirmation_url')
        widgets = {'date': date_input()}


class EnrollmentInfoForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ('municipality', 'sex')


class AttendanceForm(forms.Form):
    present = forms.ModelMultipleChoiceField(label='Присутствовали на занятии', queryset=Profile.objects.none(),
                                            required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, session, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        people = role_profiles('athlete').filter(training_enrollments__group=session.group,
                                               training_enrollments__start_date__lte=session.date).filter(
            Q(training_enrollments__end_date__isnull=True) | Q(training_enrollments__end_date__gte=session.date))
        if role == 'coach':
            people = people.filter(coach_links__coach=session.group.coach, coach_links__discipline=session.group.discipline,
                                   coach_links__status='active')
        self.fields['present'].queryset = people.distinct()
        self.fields['present'].initial = list(session.attendance.filter(present=True).values_list('athlete_id', flat=True))


class NormForm(forms.ModelForm):
    class Meta:
        model = AssessmentNorm
        fields = ('name', 'discipline', 'stage', 'sex', 'unit', 'threshold', 'direction', 'required', 'active', 'source', 'demonstration')


class AssessmentForm(forms.ModelForm):
    class Meta:
        model = AssessmentResult
        fields = ('athlete', 'norm', 'value', 'tested_on')
        widgets = {'tested_on': date_input()}

    def __init__(self, *args, group, user, role, **kwargs):
        super().__init__(*args, **kwargs)
        candidates = role_profiles('athlete').filter(training_enrollments__group=group)
        if role == 'coach':
            candidates = candidates.filter(coach_links__coach=group.coach, coach_links__discipline=group.discipline,
                                           coach_links__status='active')
        self.fields['athlete'].queryset = candidates.distinct()
        self.fields['norm'].queryset = AssessmentNorm.objects.filter(discipline=group.discipline, stage=group.stage, active=True)
        self.group = group

    def clean(self):
        data = super().clean()
        athlete, day = data.get('athlete'), data.get('tested_on')
        if athlete and day and not Enrollment.objects.filter(athlete=athlete, group=self.group, start_date__lte=day).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=day)).exists():
            self.add_error('athlete', 'На дату сдачи спортсмен не был зачислен в эту группу.')
        return data


class TransferForm(forms.Form):
    target_group = forms.ModelChoiceField(label='Группа следующего этапа', queryset=TrainingGroup.objects.none())
    date = forms.DateField(label='Дата перевода', initial=timezone.localdate, widget=date_input())

    def __init__(self, *args, enrollment, **kwargs):
        super().__init__(*args, **kwargs)
        stages = list(Stage.values)
        index = stages.index(enrollment.group.stage)
        if index + 1 < len(stages):
            self.fields['target_group'].queryset = TrainingGroup.objects.filter(active=True, discipline=enrollment.group.discipline, stage=stages[index + 1])
