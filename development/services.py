from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils import timezone

from federation.models import CoachLink, Result
from federation.access import active_role
from .models import RankCriterion


def can_access_result(request, result):
    role, app = active_role(request), result.application
    return role == 'federation' or (role == 'athlete' and app.athlete.user_id == request.user.pk) or (
        role == 'coach' and CoachLink.objects.filter(coach__user=request.user, athlete=app.athlete,
                                                    discipline=app.discipline, status='active').exists())


def rank_candidates(profile_ids=None):
    results = Result.objects.filter(application__status='approved', application__competition__protocol_published=True,
                                    application__athlete__status='active').select_related(
        'application__athlete__user', 'application__athlete__organization', 'application__competition', 'application__discipline')
    if profile_ids is not None:
        results = results.filter(application__athlete_id__in=profile_ids)
    rules = list(RankCriterion.objects.filter(active=True).select_related('discipline'))
    matches = []
    for result in results:
        athlete = result.application.athlete
        if athlete.disqualified_until and athlete.disqualified_until >= timezone.localdate():
            continue
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
