import logging
from datetime import date
from decimal import Decimal

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access import active_role, role_profiles
from .models import AuditLog, Competition, Notification, Profile, RankApplication, RatingRule, Result

logger = logging.getLogger(__name__)


def audit(request, action, entity='', detail=''):
    return AuditLog.objects.create(user=request.user, role=active_role(request) or '', action=action,
                                   entity=str(entity), detail=detail)


def notify(user, title, body, url=''):
    notice = Notification.objects.create(user=user, title=title, body=body, url=url)

    def deliver():
        if not user.email:
            return
        try:
            if send_mail(title, body, None, [user.email], fail_silently=False):
                Notification.objects.filter(pk=notice.pk).update(email_sent=True)
        except Exception:
            # In-app delivery remains available if an external mail server is unavailable.
            logger.warning('Email delivery failed for notification %s', notice.pk)
    transaction.on_commit(deliver)
    return notice


def integer(value, default, minimum=0, maximum=9999):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def calculate_rating(season, discipline_id=None, age_min=None, age_max=None, min_rank=''):
    season = integer(season, timezone.localdate().year, 1900, 2100)
    athletes = role_profiles('athlete').filter(status='active').filter(
        Q(disqualified_until__isnull=True) | Q(disqualified_until__lt=timezone.localdate()))
    if min_rank:
        ranks = ['III юношеский', 'II юношеский', 'I юношеский',
                 'III разряд', 'II разряд', 'I разряд', 'КМС', 'МС', 'МСМК']
        if min_rank not in ranks:
            return []
        athletes = athletes.filter(rank_applications__kind='rank', rank_applications__status='approved',
                                   rank_applications__valid_until__gte=timezone.localdate(),
                                   rank_applications__requested_title__in=ranks[ranks.index(min_rank):]).distinct()
    people = {p.pk: p for p in athletes.prefetch_related('disciplines')}
    age_day = date(season, 12, 31)
    for pk, person in list(people.items()):
        age = person.age_on(age_day)
        if (age_min is not None and (age is None or age < int(age_min))) or (age_max is not None and (age is None or age > int(age_max))):
            del people[pk]
    results = Result.objects.filter(application__athlete_id__in=people,
                                    application__status='approved', application__competition__protocol_published=True,
                                    application__competition__starts_on__year=season).select_related('application__competition', 'application__discipline')
    if discipline_id:
        results = results.filter(application__discipline_id=discipline_id)
    defaults = {'regional': (40, 30, 20), 'interregional': (60, 45, 30),
                'russian_championship': (100, 80, 60), 'russian_junior': (80, 60, 40)}
    rules = {r.level: (r.first, r.second, r.third) for r in RatingRule.objects.all()}
    entries = {}
    for result in results:
        comp = result.application.competition
        pk = result.application.athlete_id
        entry = entries.setdefault(pk, {'athlete': people[pk], 'points': Decimal('0'), 'coefficient': Decimal('1'), 'breakdown': [], '_podiums': set()})
        base = (rules.get(comp.level) or defaults[comp.level])[result.place - 1] if result.place <= 3 else 0
        entry['points'] += Decimal(base)
        if result.place <= 3:
            entry['_podiums'].add(comp.pk)
        entry['breakdown'].append({'competition': comp, 'title': comp.title, 'place': result.place,
                                   'discipline': result.application.discipline, 'base': base})
    for entry in entries.values():
        if len(entry.pop('_podiums')) >= 2:
            entry['coefficient'] = Decimal('1.2')
        entry['points'] = (entry['points'] * entry['coefficient']).quantize(Decimal('.1'))
    return sorted(entries.values(), key=lambda e: (-e['points'], e['athlete'].display_name, e['athlete'].pk))


def census(year_from, year_to, discipline=None, organization=None):
    # Participation intervals are explicit; organization/discipline reflect the current registry.
    people = role_profiles('athlete')
    if discipline:
        people = people.filter(disciplines=discipline)
    if organization:
        people = people.filter(organization=organization)
    return [{'year': year, 'total': people.filter(start_year__lte=year).filter(
        Q(end_year__isnull=True) | Q(end_year__gte=year)).distinct().count()}
            for year in range(year_from, year_to + 1)]
