from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from federation.models import CoachLink, Profile
from .models import AssessmentNorm, AssessmentResult, Attestation, Enrollment, Stage


def actor_can_manage_athlete(user, role, group, athlete):
    return role == 'federation' or (role == 'coach' and group.coach.user_id == user.pk and CoachLink.objects.filter(coach=group.coach, athlete=athlete, discipline=group.discipline, status='active').exists())


def readiness(enrollment, on_date=None):
    day = on_date or timezone.localdate()
    norms = AssessmentNorm.objects.filter(discipline=enrollment.group.discipline, stage=enrollment.group.stage, active=True, required=True, sex__in=['all', enrollment.sex])
    rows = []
    for norm in norms:
        result = AssessmentResult.objects.filter(athlete=enrollment.athlete, norm=norm, tested_on__range=(day - timedelta(days=365), day)).order_by('-tested_on', '-pk').first()
        rows.append({'norm': norm, 'result': result, 'passed': bool(result is not None and norm.compare(result.value))})
    missing_sex = enrollment.sex == 'unspecified' and AssessmentNorm.objects.filter(
        discipline=enrollment.group.discipline, stage=enrollment.group.stage, active=True, required=True,
        sex__in=['female', 'male']).exists()
    return {'ready': bool(rows) and not missing_sex and all(row['passed'] for row in rows), 'requirements': rows,
            'missing_norms': not bool(rows), 'missing_sex': missing_sex}


@transaction.atomic
def enroll(athlete, group, start_date, municipality, sex='unspecified', end_date=None):
    Profile.objects.select_for_update().get(pk=athlete.pk)
    if not group.active or athlete.status != 'active':
        raise ValidationError('Зачисление возможно только в действующую группу для действующего спортсмена.')
    record = Enrollment(athlete=athlete, group=group, start_date=start_date, end_date=end_date, municipality=municipality, sex=sex)
    record.full_clean()
    record.save()
    return record


@transaction.atomic
def transfer_enrollment(enrollment, target_group, on_date, actor):
    old = Enrollment.objects.select_for_update().select_related('group', 'athlete').get(pk=enrollment.pk)
    Profile.objects.select_for_update().get(pk=old.athlete_id)
    if old.end_date is not None or hasattr(old, 'attestation'):
        raise ValidationError('Это зачисление уже завершено.')
    if on_date <= old.start_date:
        raise ValidationError('Перевод возможен начиная со дня после зачисления: один день не может относиться к двум группам.')
    if on_date > timezone.localdate():
        raise ValidationError('Перевод фиксируется на текущую или прошедшую дату.')
    if target_group.discipline_id != old.group.discipline_id:
        raise ValidationError('Перевод по аттестации выполняется в той же дисциплине.')
    stages = list(Stage.values)
    position = stages.index(old.group.stage)
    if position == len(stages) - 1 or target_group.stage != stages[position + 1]:
        raise ValidationError('Выберите группу непосредственно следующего этапа подготовки.')
    ready = readiness(old, on_date)
    if not ready['ready']:
        raise ValidationError('Не выполнены все обязательные действующие нормативы текущего этапа за последние 365 дней.')
    # Do not retroactively move someone out of an already recorded lesson/result.
    if old.athlete.training_attendance.filter(session__group=old.group, session__date__gte=on_date).exists():
        raise ValidationError('После даты перевода уже есть посещения в прежней группе. Выберите более позднюю дату.')
    if old.athlete.assessment_results.filter(norm__discipline=old.group.discipline, norm__stage=old.group.stage, tested_on__gt=on_date).exists():
        raise ValidationError('После даты перевода уже есть сдача нормативов прежнего этапа.')
    old.end_date = on_date - timedelta(days=1)
    old.full_clean()
    old.save(update_fields=['end_date'])
    new = enroll(old.athlete, target_group, on_date, old.municipality, old.sex)
    evidence = [{'norm_id': item['norm'].pk, 'result_id': item['result'].pk,
                 'value': str(item['result'].value), 'threshold': str(item['norm'].threshold),
                 'direction': item['norm'].direction} for item in ready['requirements']]
    return Attestation.objects.create(enrollment=old, destination=target_group, new_enrollment=new,
                                      date=on_date, recorded_by=actor, evidence=evidence)


def participation_rows(start, end, discipline=None, organization=None, age_min=None, age_max=None, municipality=None):
    """Historical census facts, deduplicated by athlete/year/organization/discipline.

    Dates are inclusive; ages are measured on December 31 of the census year.
    Consumers counting regional totals should distinct athlete_id per year.
    Profiles with no enrollment history can be counted via their legacy interval.
    """
    queryset = Enrollment.objects.filter(start_date__lte=date(end, 12, 31)).filter(Q(end_date__isnull=True) | Q(end_date__gte=date(start, 1, 1))).select_related('athlete', 'group')
    if discipline:
        queryset = queryset.filter(group__discipline_id=discipline)
    if organization:
        queryset = queryset.filter(group__organization_id=organization)
    if municipality:
        queryset = queryset.filter(municipality__iexact=municipality)
    facts = {}
    for enrollment in queryset:
        for year in range(max(start, enrollment.start_date.year), min(end, enrollment.end_date.year if enrollment.end_date else end) + 1):
            age = enrollment.athlete.age_on(date(year, 12, 31))
            if age_min is not None and (age is None or age < age_min):
                continue
            if age_max is not None and (age is None or age > age_max):
                continue
            key = (year, enrollment.athlete_id, enrollment.group.organization_id, enrollment.group.discipline_id)
            facts[key] = {'year': year, 'athlete_id': enrollment.athlete_id, 'organization_id': enrollment.group.organization_id,
                          'discipline_id': enrollment.group.discipline_id, 'municipality': enrollment.municipality,
                          'stage': enrollment.group.stage, 'age': age}
    return list(facts.values())
