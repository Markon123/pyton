import logging
from datetime import date
from decimal import Decimal

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.utils import timezone

from .access import active_role, role_profiles
from .models import AuditLog, Competition, Notification, Profile, RankApplication, RatingRule, Result

logger = logging.getLogger(__name__)


def audit(request, action, entity='', detail=''):
    entry = AuditLog.objects.create(user=request.user, role=active_role(request) or '', action=action,
                                   entity=str(entity), detail=detail)
    from integrations.services import emit_event
    emit_event(action, str(entity), detail, actor=request.user)
    return entry


def notify(user, title, body, url=''):
    notice = Notification.objects.create(user=user, title=title, body=body, url=url)

    def deliver():
        from .mail_delivery import deliver_notification
        deliver_notification(notice.pk)
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
    from governance.services import active_disqualifications
    athletes = athletes.exclude(pk__in=active_disqualifications().values('athlete_id'))
    if min_rank:
        from .forms import RANK_TITLES, configured_titles
        ranks = configured_titles('rank', RANK_TITLES)
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
    from governance.models import ViolationRecord
    invalid_result = ViolationRecord.objects.filter(athlete_id=OuterRef('application__athlete_id'),
        competition_id=OuterRef('application__competition_id'), status='confirmed', starts_on__lte=timezone.localdate(), decision__in=['technical_defeat', 'disqualification'])
    results = results.annotate(_invalid=Exists(invalid_result)).filter(_invalid=False)
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


def census_facts(year_from, year_to, discipline=None, organization=None, age_min=None, age_max=None, municipality=None):
    from training.models import Enrollment
    from training.services import participation_rows
    facts = participation_rows(year_from, year_to, discipline, organization, age_min, age_max, municipality)
    # Historical enrollment facts take priority; do not project today's affiliations into past years.
    people = role_profiles('athlete').exclude(pk__in=Enrollment.objects.values('athlete_id')).prefetch_related('disciplines')
    if discipline:
        people = people.filter(disciplines=discipline)
    if organization:
        people = people.filter(organization=organization)
    if municipality:
        people = people.filter(municipality__iexact=municipality)
    for person in people:
        for year in range(max(year_from, person.start_year), min(year_to, person.end_year or year_to) + 1):
            age = person.age_on(date(year, 12, 31))
            if age_min is not None and (age is None or age < age_min):
                continue
            if age_max is not None and (age is None or age > age_max):
                continue
            for disc in person.disciplines.all():
                if not discipline or disc.pk == int(discipline):
                    facts.append({'year': year, 'athlete_id': person.pk, 'organization_id': person.organization_id,
                                  'discipline_id': disc.pk, 'municipality': person.municipality,
                                  'stage': '', 'age': age})
    return facts


def census(year_from, year_to, discipline=None, organization=None, age_min=None, age_max=None, municipality=None):
    facts = census_facts(year_from, year_to, discipline, organization, age_min, age_max, municipality)
    return [{'year': year, 'total': len({f['athlete_id'] for f in facts if f['year'] == year})}
            for year in range(year_from, year_to + 1)]


def census_breakdown(year_from, year_to, discipline=None, organization=None, age_min=None, age_max=None, municipality=None):
    from .models import Discipline, Organization
    names = {'organization_id': dict(Organization.objects.values_list('pk', 'name')),
             'discipline_id': dict(Discipline.objects.values_list('pk', 'name'))}
    facts = census_facts(year_from, year_to, discipline, organization, age_min, age_max, municipality)
    buckets = {}
    for fact in facts:
        age = fact['age']
        age_group = 'Не указан' if age is None else ('До 14 лет' if age < 14 else '14–17 лет' if age < 18 else '18–25 лет' if age < 26 else '26 лет и старше')
        for dim in ('organization_id', 'discipline_id', 'municipality', 'age_group'):
            key = age_group if dim == 'age_group' else fact[dim]
            buckets.setdefault((fact['year'], dim, key), set()).add(fact['athlete_id'])
    labels = {'organization_id': 'Организация', 'discipline_id': 'Дисциплина', 'municipality': 'Муниципалитет', 'age_group': 'Возрастная группа'}
    return [{'year': year, 'dimension': labels[dim], 'label': names.get(dim, {}).get(key, 'Без организации') if dim in names else key or 'Не указан', 'total': len(ids)}
            for (year, dim, key), ids in sorted(buckets.items(), key=lambda pair: (pair[0][0], pair[0][1], str(pair[0][2])))]


def layout_document(kind, title, subtitle=''):
    from .models import DocumentLayout
    layout = DocumentLayout.objects.filter(kind=kind).first()
    if layout:
        title = ' · '.join(filter(None, [layout.title_prefix, title]))
        subtitle = '\n'.join(filter(None, [layout.heading, subtitle, layout.footer]))
    return title, subtitle
