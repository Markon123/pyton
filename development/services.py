from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils import timezone

from federation.models import CoachLink, Organization, RankApplication, Result
from federation.access import active_role
from .models import RankCriterion


def preparation_organizations():
    """Current organizations with a program or an active training group.

    Registry membership alone does not prove that preparation is offered.
    Programs/groups have no historical validity interval, so this is explicitly
    a current-data indicator rather than a reconstruction for the selected year.
    """
    from django.db.models import Q
    return Organization.objects.filter(
        Q(training_programs__isnull=False) | Q(training_groups__active=True)
    ).distinct().order_by('name', 'pk')


def athlete_rank_path(person, on_date=None):
    """Next configured step independently for each athlete discipline.

    Awarded ranks are attributed through their source result. Expired awards
    are not presented as current: their own title is offered for confirmation,
    with a visible warning, instead of sending the athlete to a lower grade.
    Missing/retired dictionary entries are not guessed or silently reordered.
    """
    from federation.forms import RANK_TITLES, configured_titles
    day = on_date or timezone.localdate()
    titles = list(dict.fromkeys(configured_titles('rank', RANK_TITLES)))
    positions = {title: index for index, title in enumerate(titles)}
    disciplines = list(person.disciplines.all().order_by('name', 'pk'))
    awards = list(RankApplication.objects.filter(
        athlete=person, kind='rank', status='approved', assigned_on__lte=day,
    ).select_related('result__application__discipline').order_by('-assigned_on', '-pk'))
    criteria = list(RankCriterion.objects.filter(
        active=True, discipline__in=disciplines,
    ).select_related('discipline').order_by('pk'))
    age = person.age_on(day)
    unscoped = [award for award in awards if award.result_id is None]
    steps = []
    for discipline in disciplines:
        own_awards = [award for award in awards if award.result_id is not None
                      and award.result.application.discipline_id == discipline.pk]
        active = [award for award in own_awards if award.valid_until and award.valid_until >= day]
        unknown = [award for award in active if award.requested_title not in positions]
        current = max(active, key=lambda award: positions.get(award.requested_title, -1), default=None)
        historic = max(own_awards, key=lambda award: positions.get(award.requested_title, -1), default=None)
        own_criteria = [rule for rule in criteria if rule.discipline_id == discipline.pk]
        step = {'discipline': discipline, 'current': current, 'expired': None,
                'target_title': None, 'criteria': [], 'status': 'not_configured'}
        if unknown or unscoped:
            step['status'] = 'unknown_rank'
        elif current:
            index = positions[current.requested_title]
            if index + 1 < len(titles):
                step['target_title'] = titles[index + 1]
            else:
                step['status'] = 'highest_configured'
        elif historic:
            step['expired'] = historic
            if historic.requested_title in positions:
                step['target_title'] = historic.requested_title
                step['status'] = 'confirmation'
            else:
                step['status'] = 'unknown_rank'
        elif titles:
            eligible = {rule.rank_title for rule in own_criteria
                        if rule.rank_title in positions and age is not None
                        and rule.age_min <= age <= rule.age_max}
            step['target_title'] = min(eligible, key=positions.get) if eligible else titles[0]
        if step['target_title']:
            step['criteria'] = [rule for rule in own_criteria if rule.rank_title == step['target_title']]
            if step['status'] != 'confirmation':
                step['status'] = 'ready' if step['criteria'] else 'not_configured'
        steps.append(step)
    return steps


def can_access_result(request, result):
    role, app = active_role(request), result.application
    return role == 'federation' or (role == 'athlete' and app.athlete.user_id == request.user.pk) or (
        role == 'coach' and CoachLink.objects.filter(coach__user=request.user, athlete=app.athlete,
                                                    discipline=app.discipline, status='active').exists())


def rank_candidates(profile_ids=None, on_date=None):
    from governance.services import valid_results
    results = Result.objects.filter(application__status='approved', application__competition__protocol_published=True,
                                    application__athlete__status='active').select_related(
        'application__athlete__user', 'application__athlete__organization', 'application__competition', 'application__discipline')
    if profile_ids is not None:
        results = results.filter(application__athlete_id__in=profile_ids)
    results = valid_results(results, on_date, exclude_active_disqualified=True)
    rules = list(RankCriterion.objects.filter(active=True).select_related('discipline'))
    matches = []
    for result in results:
        athlete = result.application.athlete
        for rule in rules:
            if rule.matches(result):
                matches.append({'athlete': athlete, 'result': result, 'criterion': rule})
    return matches


def certificate_url(certificate, request=None):
    path = reverse('development:verify', args=[certificate.number])
    base = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/')
    if base:
        return base + path
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def certificate_pdf(certificate, request):
    import qrcode
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

    font_root = Path(settings.BASE_DIR) / 'federation/assets/fonts'
    if 'FspCertificate' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('FspCertificate', str(font_root / 'DejaVuSans.ttf')))
    style = ParagraphStyle('certificate', fontName='FspCertificate', fontSize=11, leading=17)
    title_style = ParagraphStyle('certificateTitle', parent=style, fontSize=21, leading=28, textColor=colors.HexColor('#172726'))
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, rightMargin=45, leftMargin=45, topMargin=45)
    result = certificate.result
    app = result.application
    snapshot = certificate.snapshot
    url = certificate_url(certificate, request)
    qr_file = BytesIO()
    qrcode.make(url).save(qr_file, format='PNG')
    qr_file.seek(0)
    def p(text, chosen=style):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), chosen)
    story = [p('СПРАВКА ОБ УЧАСТИИ', title_style), Spacer(1, 18),
             p(f'Номер: {certificate.number}'), p(f'Дата выдачи: {timezone.localtime(certificate.issued_at):%d.%m.%Y}'),
             Spacer(1, 20), p(snapshot.get('athlete_name', app.athlete.display_name)),
             p(f'Соревнование: {snapshot.get("competition_title", app.competition.title)}'),
             p(f'Дата: {snapshot.get("competition_date", app.competition.starts_on.isoformat())}'),
             p(f'Дисциплина: {snapshot.get("discipline_name", app.discipline.name)}'),
             p(f'Результат: {snapshot.get("place", result.place)} место, {snapshot.get("score", str(result.score))} баллов'), Spacer(1, 20),
             p('Действительна' if certificate.valid else 'НЕДЕЙСТВИТЕЛЬНА / ОТОЗВАНА'),
             Image(qr_file, width=135, height=135), p(f'Проверка подлинности: {url}'),
             Spacer(1, 20), p('Демонстрационная справка. Персональные данные вымышлены. Документ подтверждает запись в платформе и не заменяет подписанный официальный документ.')]
    doc.build(story)
    response = HttpResponse(out.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = content_disposition_header(True, f'certificate-{certificate.number}.pdf')
    response['Cache-Control'] = 'private, no-store'
    return response
