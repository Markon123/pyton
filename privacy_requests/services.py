from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from federation.access import role_profiles
from federation.models import (
    Application, AuditLog, CoachLink, Consent, JudgeAssignment, Notification,
    RankApplication, Result, Team,
)
from federation.services import audit, notify

from .models import PrivacyRequest, PrivacyRequestEvent


OPEN_STATUSES = [PrivacyRequest.Status.REQUESTED, PrivacyRequest.Status.REVIEWING, PrivacyRequest.Status.APPROVED]


@transaction.atomic
def create_request(request, reason):
    existing = PrivacyRequest.objects.filter(user=request.user, status__in=OPEN_STATUSES).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            record = PrivacyRequest.objects.create(user=request.user, reason=reason)
    except IntegrityError:
        # The partial unique constraint also protects two simultaneous submits.
        return PrivacyRequest.objects.get(user=request.user, status__in=OPEN_STATUSES), False
    PrivacyRequestEvent.objects.create(request=record, actor=request.user, status=record.status, comment=reason)
    audit(request, 'Подано обращение об удалении данных', f'privacy_request:{record.pk}')
    for person in role_profiles('federation').filter(user__is_active=True).exclude(user=request.user):
        notify(person.user, 'Обращение по персональным данным', f'Получено обращение № {record.pk}.',
               reverse('privacy_requests:detail', args=[record.pk]))
    return record, True


@transaction.atomic
def review_request(request, record_id, status, decision_reason):
    record = PrivacyRequest.objects.select_for_update().get(pk=record_id)
    if not record.can_review:
        from django.core.exceptions import ValidationError
        raise ValidationError('По этому обращению уже сохранено решение.')
    allowed = {PrivacyRequest.Status.APPROVED, PrivacyRequest.Status.REJECTED}
    if record.status == PrivacyRequest.Status.REQUESTED:
        allowed.add(PrivacyRequest.Status.REVIEWING)
    if status not in allowed or not decision_reason.strip():
        from django.core.exceptions import ValidationError
        raise ValidationError('Укажите допустимое решение и ответ заявителю.')
    previous = record.status
    record.status = status
    record.decision_reason = decision_reason.strip()
    record.reviewed_by = request.user
    record.reviewed_at = timezone.now()
    record.save(update_fields=['status', 'decision_reason', 'reviewed_by', 'reviewed_at', 'updated_at'])
    PrivacyRequestEvent.objects.create(request=record, actor=request.user, status=status, comment=record.decision_reason)
    audit(request, 'Рассмотрено обращение по персональным данным', f'privacy_request:{record.pk}', f'{previous} → {status}')
    if record.user_id:
        notify(record.user, 'Ответ на обращение по персональным данным',
               f'Обращение № {record.pk}: {record.get_status_display()}.',
               reverse('privacy_requests:detail', args=[record.pk]))
    return record


def _competition(competition):
    return {'id': competition.pk, 'title': competition.title, 'starts_on': competition.starts_on,
            'ends_on': competition.ends_on, 'state': competition.state,
            'protocol_published': competition.protocol_published}


def _discipline(discipline):
    return {'id': discipline.pk, 'name': discipline.name, 'code': discipline.code}


def _group(group):
    return {'id': group.pk, 'name': group.name, 'organization_id': group.organization_id,
            'organization': group.organization.name, 'discipline': _discipline(group.discipline),
            'stage': group.stage, 'coach_profile_id': group.coach_id}


def personal_data(user):
    """Explicitly serialize the subject's records, never arbitrary model fields.

    Related people are represented only by IDs already part of the subject's
    relationship. Their contact details, birth dates, credentials and files are
    not included. Authentication providers and transport credentials are not
    queried at all.
    """
    data = {
        'schema_version': 1, 'generated_at': timezone.now(),
        'account': {name: getattr(user, name) for name in (
            'id', 'username', 'first_name', 'last_name', 'email', 'date_joined', 'last_login', 'is_active')},
        'consents': list(Consent.objects.filter(user=user).values('id', 'kind', 'representative_name', 'version', 'accepted_at')),
        'profile': None,
        'notifications': list(Notification.objects.filter(user=user).values('id', 'title', 'body', 'url', 'read', 'created_at')),
        # Audit details can contain other subjects' names; only the user's action
        # metadata is exported here.
        'actions': list(AuditLog.objects.filter(user=user).values('id', 'role', 'action', 'entity', 'created_at')),
        'privacy_requests': list(PrivacyRequest.objects.filter(user=user).values(
            'id', 'reason', 'status', 'decision_reason', 'created_at', 'updated_at', 'reviewed_at')),
        'submitted_applications': list(Application.objects.filter(submitted_by=user).values(
            'id', 'competition_id', 'athlete_id', 'discipline_id', 'status', 'created_at')),
    }
    person = getattr(user, 'profile', None)
    if person is None:
        return data
    data['profile'] = {name: getattr(person, name) for name in (
        'id', 'roles', 'birth_date', 'municipality', 'organization_id', 'start_year', 'end_year',
        'status', 'disqualified_until', 'guardian_name', 'anti_doping_date', 'anti_doping_url')}
    data['profile']['disciplines'] = [_discipline(value) for value in person.disciplines.all()]
    if person.organization_id:
        data['profile']['organization'] = {name: getattr(person.organization, name) for name in ('id', 'name', 'kind', 'municipality')}
    applications = Application.objects.filter(athlete=person).select_related('competition', 'discipline')
    data['applications'] = [{'id': value.pk, 'competition': _competition(value.competition),
                             'discipline': _discipline(value.discipline), 'status': value.status,
                             'comment': value.comment, 'created_at': value.created_at,
                             'submitted_by_id': value.submitted_by_id} for value in applications]
    results = Result.objects.filter(application__athlete=person).select_related('application__competition', 'application__discipline')
    data['results'] = [{'id': value.pk, 'application_id': value.application_id,
                       'competition': _competition(value.application.competition),
                       'discipline': _discipline(value.application.discipline),
                       'place': value.place, 'score': value.score} for value in results]
    data['qualifications'] = list(RankApplication.objects.filter(athlete=person).values(
        'id', 'result_id', 'kind', 'requested_title', 'status', 'review_comment', 'assigned_on',
        'valid_until', 'order_number', 'created_at', 'submitted_by_id'))
    data['coach_links'] = list(CoachLink.objects.filter(Q(coach=person) | Q(athlete=person)).values(
        'id', 'coach_id', 'athlete_id', 'discipline_id', 'status', 'created_at', 'updated_at'))
    data['judge_assignments'] = [{'id': value.pk, 'competition': _competition(value.competition),
                                  'position': value.position} for value in JudgeAssignment.objects.filter(judge=person).select_related('competition')]
    data['teams'] = list(Team.objects.filter(members=person).values('id', 'season', 'discipline_id', 'age_min', 'age_max', 'created_at'))

    from training.models import AssessmentResult, Attendance, Attestation, Enrollment
    data['enrollments'] = [{'id': value.pk, 'group': _group(value.group), 'start_date': value.start_date,
                            'end_date': value.end_date, 'municipality': value.municipality,
                            'sex': value.sex, 'created_at': value.created_at}
                           for value in Enrollment.objects.filter(athlete=person).select_related('group__organization', 'group__discipline')]
    data['attendance'] = [{'id': value.pk, 'session_id': value.session_id, 'date': value.session.date,
                           'topic': value.session.topic, 'group_id': value.session.group_id,
                           'present': value.present}
                          for value in Attendance.objects.filter(athlete=person).select_related('session')]
    data['assessments'] = [{'id': value.pk, 'tested_on': value.tested_on, 'value': value.value,
                           'norm_id': value.norm_id, 'norm_name': value.norm.name,
                           'discipline_id': value.norm.discipline_id, 'stage': value.norm.stage,
                           'unit': value.norm.unit, 'threshold_snapshot': value.threshold_snapshot,
                           'direction_snapshot': value.direction_snapshot, 'passed': value.passed}
                          for value in AssessmentResult.objects.filter(athlete=person).select_related('norm')]
    data['attestations'] = list(Attestation.objects.filter(enrollment__athlete=person).values(
        'id', 'enrollment_id', 'destination_id', 'new_enrollment_id', 'date', 'evidence', 'created_at'))

    from governance.models import Appeal, AwardPetition, ViolationRecord
    data['violations'] = list(ViolationRecord.objects.filter(athlete=person).values(
        'id', 'competition_id', 'reason', 'decision', 'starts_on', 'ends_on', 'status', 'created_at'))
    data['appeals'] = list(Appeal.objects.filter(Q(result__application__athlete=person) | Q(violation__athlete=person)).values(
        'id', 'result_id', 'violation_id', 'reason', 'status', 'decision_reason', 'reviewed_at', 'created_at'))
    data['award_petitions'] = list(AwardPetition.objects.filter(athlete=person).values(
        'id', 'year', 'result_id', 'rule_id', 'amount', 'status', 'passport_mask', 'inn_mask',
        'snils_mask', 'account_mask', 'comment', 'created_at', 'updated_at'))
    from development.models import ParticipationCertificate
    data['participation_certificates'] = list(ParticipationCertificate.objects.filter(result__application__athlete=person).values(
        'id', 'number', 'result_id', 'issued_at', 'revoked', 'public_consent', 'snapshot'))
    return data
