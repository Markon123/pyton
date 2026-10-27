from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from federation.models import CoachLink, Competition, Profile, Result
from federation.services import audit, notify
from .models import Appeal, AwardPetition, AwardRule, ProtocolAmendment, ViolationRecord


def active_disqualifications(day=None):
    day = day or timezone.localdate()
    return ViolationRecord.objects.filter(decision='disqualification', status='confirmed', starts_on__lte=day, ends_on__gte=day)


def valid_results(queryset, on_date=None, *, exclude_active_disqualified=False):
    """Published results not voided by an effective, confirmed decision.

    A competition-specific technical defeat/disqualification invalidates that
    performance after its effective date, until the decision is overturned.
    Its expiry ends a suspension; it does not restore the invalid performance.
    Current candidate/ranking workflows can additionally exclude athletes with
    an active global suspension. Historical participation certificates and awards
    remain scoped to their source competition, rather than unrelated later bans.
    """
    day = on_date or timezone.localdate()
    voided = ViolationRecord.objects.filter(
        athlete_id=OuterRef('application__athlete_id'),
        competition_id=OuterRef('application__competition_id'),
        status='confirmed', starts_on__lte=day,
        decision__in=['technical_defeat', 'disqualification'])
    queryset = queryset.filter(application__status='approved', application__competition__protocol_published=True)
    queryset = queryset.alias(_governance_result_void=Exists(voided)).filter(_governance_result_void=False)
    if exclude_active_disqualified:
        queryset = queryset.exclude(application__athlete_id__in=active_disqualifications(day).values('athlete_id'))
        queryset = queryset.exclude(application__athlete__disqualified_until__gte=day)
    return queryset


def may_appeal(request, person, competition, discipline=None):
    from federation.access import active_role
    role = active_role(request)
    if role == 'athlete':
        return person.user_id == request.user.pk
    if role == 'coach':
        links = CoachLink.objects.filter(coach__user=request.user, athlete=person, status='active', discipline__in=competition.disciplines.all())
        if discipline is not None:
            links = links.filter(discipline=discipline)
        return links.exists()
    return False


@transaction.atomic
def review_appeal(request, appeal, status, reason, place=None, score=None):
    appeal = Appeal.objects.select_for_update(of=('self',)).select_related('result__application__competition', 'violation__athlete__user').get(pk=appeal.pk)
    if appeal.status not in ('pending', 'reviewed'):
        raise ValidationError('Решение по апелляции уже вынесено.')
    if status not in ('reviewed', 'rejected', 'upheld'):
        raise ValidationError('Недопустимый статус апелляции.')
    if not reason.strip():
        raise ValidationError('Введите мотивированное решение.')
    if status == 'upheld' and appeal.result_id:
        # Every result writer shares this competition lock. It serializes place
        # uniqueness checks even when two different results are amended.
        Competition.objects.select_for_update().get(pk=appeal.result.application.competition_id)
        result = Result.objects.select_for_update().select_related('application__competition').get(pk=appeal.result_id)
        old = {'place': result.place, 'score': str(result.score)}
        if place is not None:
            result.place = place
        if score is not None:
            result.score = score
        if result.score < 0:
            raise ValidationError('Баллы не могут быть отрицательными.')
        result.full_clean()
        result.save(update_fields=['place', 'score'])
        ProtocolAmendment.objects.create(competition=result.application.competition, result=result, appeal=appeal,
            old_value=old, new_value={'place': result.place, 'score': str(result.score)}, reason=reason, amended_by=request.user)
    elif status == 'upheld' and appeal.violation_id:
        violation = ViolationRecord.objects.select_for_update().select_related('athlete').get(pk=appeal.violation_id)
        violation.status = 'overturned'
        violation.save(update_fields=['status'])
        person = Profile.objects.select_for_update().get(pk=violation.athlete_id)
        if person.disqualified_until == violation.ends_on:
            remaining = active_disqualifications().filter(athlete=person).order_by('-ends_on').first()
            prior = violation.previous_until
            if prior and ViolationRecord.objects.filter(athlete=person, decision='disqualification', ends_on=prior).exists():
                # A historical copied expiry is not a fresh manual sanction.
                # In particular, do not restore a decision already overturned.
                prior = None
            person.disqualified_until = max(filter(None, [remaining.ends_on if remaining else None, prior]), default=None)
            person.save(update_fields=['disqualified_until'])
    appeal.status, appeal.decision_reason = status, reason
    appeal.reviewed_by, appeal.reviewed_at = request.user, timezone.now()
    appeal.full_clean()
    appeal.save()
    audit(request, 'Решение по апелляции', f'appeal:{appeal.pk}', f'{status}: {reason}')
    notify(appeal.athlete.user, 'Решение по апелляции', f'{appeal.get_status_display()}: {reason}', f'/governance/appeals/{appeal.pk}/')
    return appeal


@transaction.atomic
def record_violation(request, violation):
    person = Profile.objects.select_for_update().get(pk=violation.athlete_id)
    violation.recorded_by = request.user
    violation.previous_until = person.disqualified_until
    violation.full_clean()
    violation.save()
    if violation.decision == 'disqualification' and violation.starts_on <= timezone.localdate() <= violation.ends_on:
        person.disqualified_until = max(filter(None, [person.disqualified_until, violation.ends_on]))
        person.save(update_fields=['disqualified_until'])
    audit(request, 'Зафиксировано нарушение', f'violation:{violation.pk}', violation.reason)
    notify(person.user, 'Решение по нарушению', f'{violation.get_decision_display()}: {violation.reason}', '/governance/appeals/')
    return violation


def award_candidates(year, on_date=None):
    rules = {(r.level, r.place): r for r in AwardRule.objects.filter(active=True)}
    candidates = {}
    results = Result.objects.filter(application__competition__protocol_published=True, application__status='approved',
        application__competition__starts_on__year=year, place__lte=3).select_related('application__athlete__user', 'application__competition', 'application__discipline')
    results = valid_results(results, on_date)
    for result in results:
        comp, person = result.application.competition, result.application.athlete
        rule = rules.get((comp.level, result.place))
        if not rule:
            continue
        item = {'athlete': person, 'result': result, 'rule': rule, 'amount': rule.amount, 'breakdown': []}
        breakdown = {'competition': comp.title, 'competition_id': comp.pk, 'result_id': result.pk,
            'place': result.place, 'discipline': result.application.discipline.name, 'amount': str(rule.amount),
            'rule_id': rule.pk, 'source': rule.source, 'demonstration': rule.demonstration}
        previous = candidates.get(person.pk)
        if previous:
            item['breakdown'] = previous['breakdown']
            if previous['amount'] > rule.amount or (previous['amount'] == rule.amount and previous['result'].pk < result.pk):
                previous['breakdown'].append(breakdown)
                continue
        item['breakdown'].append(breakdown)
        candidates[person.pk] = item
    return sorted(candidates.values(), key=lambda v: (-v['amount'], v['athlete'].display_name))


@transaction.atomic
def prepare_petition(request, year, athlete_id):
    candidate = next((c for c in award_candidates(year) if c['athlete'].pk == athlete_id), None)
    if candidate is None:
        raise ValidationError('Не найдено подходящего опубликованного результата по действующим правилам премирования.')
    petition, created = AwardPetition.objects.get_or_create(athlete=candidate['athlete'], year=year, defaults={
        'result': candidate['result'], 'rule': candidate['rule'], 'amount': candidate['amount'], 'created_by': request.user,
        'provenance': {'policy': 'best_result', 'results': candidate['breakdown'], 'demonstration': candidate['rule'].demonstration}})
    if created:
        audit(request, 'Подготовлено ходатайство', f'petition:{petition.pk}', f'{petition.amount} руб., {year}')
    return petition
