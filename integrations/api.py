"""Versioned JSON API. Workflow mutations reuse the same validated HTML actions."""
from copy import copy
from datetime import timedelta
import json

from django.contrib.auth import authenticate, get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError as DjangoValidationError
from django.forms.models import model_to_dict
from django.http import QueryDict
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from federation import views as workflow
from federation.access import role_profiles
from federation.forms import (CompetitionForm, FederationProfileForm, OrganizationForm,
                               ProfileForm, QualificationForm, RegistrationForm)
from federation.models import (Application, AuditLog, CoachLink, Competition, Discipline,
                               DictionaryEntry, DocumentLayout, JudgeAssignment, Notification, Organization, Profile, RankApplication,
                               RatingRule, Result, Team)
from federation.services import calculate_rating, census, integer, notify
from .authentication import require_roles, require_scope, role
from .models import PartnerToken
from .services import emit_event, import_results


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None and isinstance(exc, DjangoValidationError):
        response = Response({'error': exc.message_dict if hasattr(exc, 'message_dict') else exc.messages,
                             'code': 'validation_error'}, status=400)
    elif response is not None:
        response.data = {'error': response.data, 'code': getattr(exc, 'default_code', 'request_error')}
    return response


def form_valid(form):
    if not form.is_valid():
        raise ValidationError(form.errors.get_json_data())
    return form


def payload_dict(data):
    return {key: data.getlist(key) if key in ('roles', 'disciplines', 'members') and hasattr(data, 'getlist') else data[key] for key in data}


def json_profile(person, private=False):
    data = {'id': person.pk, 'name': person.display_name, 'roles': person.roles,
            'municipality': person.municipality, 'organization': person.organization_id,
            'disciplines': list(person.disciplines.values_list('pk', flat=True)), 'status': person.status}
    if private:
        data.update({'birth_date': person.birth_date, 'start_year': person.start_year,
                     'email': person.user.email, 'first_name': person.user.first_name,
                     'last_name': person.user.last_name, 'anti_doping_date': person.anti_doping_date,
                     'anti_doping_url': person.anti_doping_url})
    return data


def json_competition(comp):
    return {'id': comp.pk, 'title': comp.title, 'description': comp.description,
            'disciplines': list(comp.disciplines.values_list('pk', flat=True)),
            'starts_on': comp.starts_on, 'ends_on': comp.ends_on, 'location': comp.location,
            'registration_opens': comp.registration_opens, 'registration_closes': comp.registration_closes,
            'state': comp.state, 'format': comp.format, 'level': comp.level, 'kind': comp.kind,
            'age_min': comp.age_min, 'age_max': comp.age_max, 'calendar_plan': comp.calendar_plan,
            'plan_number': comp.plan_number, 'protocol_published': comp.protocol_published,
            'regulation_url': f'/competitions/{comp.pk}/regulation/' if comp.regulation else None}


def json_application(app):
    return {'id': app.pk, 'competition': app.competition_id, 'athlete': app.athlete_id,
            'discipline': app.discipline_id, 'status': app.status, 'comment': app.comment,
            'created_at': app.created_at}


def json_qualification(record):
    return {key: getattr(record, key) for key in ('id', 'athlete_id', 'result_id', 'kind', 'requested_title',
             'status', 'review_comment', 'assigned_on', 'valid_until', 'order_number')}


def people_for(request):
    current = role(request)
    if current == 'federation':
        return Profile.objects.all()
    if current == 'coach':
        return Profile.objects.filter(coach_links__coach__user=request.user, coach_links__status='active').distinct()
    return Profile.objects.filter(user=request.user)


def visible_competitions(request):
    qs = Competition.objects.prefetch_related('disciplines')
    return qs if role(request) == 'federation' else qs.exclude(state='draft')


def record_audit(request, action, entity, detail=''):
    current = role(request)
    AuditLog.objects.create(user=request.user, role='federation' if current == 'partner' else current,
                            action=action, entity=entity, detail=detail)
    emit_event(action, entity, detail, actor=request.user)


def bridge(request, view, **kwargs):
    """Keep notification/audit/state validation identical across HTML and JSON clients."""
    http = copy(request._request)
    http.user, http.method, http.active_role = request.user, 'POST', role(request)
    http.POST = QueryDict('', mutable=True)
    for key in request.data:
        value = request.data.getlist(key) if hasattr(request.data, 'getlist') else request.data[key]
        http.POST.setlist(key, [str(x) for x in value] if isinstance(value, list) else [str(value)])
    if not hasattr(http, '_messages'):
        http._messages = FallbackStorage(http)
    response = view(http, **kwargs)
    if response.has_header('X-Action-Error'):
        raise ValidationError('Переход состояния не выполнен. Проверьте состояние соревнования и полноту результатов.')
    if response.status_code >= 400:
        raise ValidationError(strip_tags(response.content.decode(errors='replace'))[:4000])
    # Business state transition views return a message redirect even for a denied transition.
    return response


def token_pair(user, selected):
    person = getattr(user, 'profile', None)
    if not person or selected not in person.roles:
        raise PermissionDenied('Эта роль не назначена аккаунту.')
    refresh = RefreshToken.for_user(user)
    refresh['active_role'] = selected
    return {'access': str(refresh.access_token), 'refresh': str(refresh), 'active_role': selected}


class BaseAPI(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.active_role = role(request)
        request._request.active_role = request.active_role

    def page(self, request, rows):
        pager = PageNumberPagination()
        pager.page_size = 25
        values = pager.paginate_queryset(rows, request, view=self)
        return values, pager


class LoginAPI(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get_authenticate_header(self, request):
        return 'Bearer'

    def post(self, request):
        user = authenticate(request, username=request.data.get('username'), password=request.data.get('password'))
        person = getattr(user, 'profile', None) if user else None
        if not user or not person or not person.roles:
            raise AuthenticationFailed('Неверный логин или пароль.')
        return Response(token_pair(user, request.data.get('role') or person.roles[0]))


class RegisterAPI(LoginAPI):
    def post(self, request):
        allowed = {'athlete', 'coach', 'judge'}
        requested = request.data.get('roles', [])
        if isinstance(requested, str):
            requested = [requested]
        if not requested or not set(requested).issubset(allowed):
            raise ValidationError('Самостоятельно доступны только роли спортсмена, тренера и судьи.')
        user = form_valid(RegistrationForm(request.data)).save()
        return Response(token_pair(user, user.profile.roles[0]), status=201)


class RefreshAPI(LoginAPI):
    def post(self, request):
        raw = request.data.get('refresh', '')
        try:
            token = RefreshToken(raw)
            user = get_user_model().objects.get(pk=token['user_id'], is_active=True)
            if token.get('active_role') not in user.profile.roles:
                raise AuthenticationFailed('Роль отозвана.')
            serializer = TokenRefreshSerializer(data={'refresh': raw})
            serializer.is_valid(raise_exception=True)
            return Response(serializer.validated_data)
        except (get_user_model().DoesNotExist, Profile.DoesNotExist, KeyError, TokenError):
            raise AuthenticationFailed('Учётная запись недоступна.')


class LogoutAPI(BaseAPI):
    def post(self, request):
        require_roles(request, 'federation', 'athlete', 'coach', 'judge', 'ministry')
        try:
            token = RefreshToken(request.data.get('refresh', ''))
        except TokenError:
            raise AuthenticationFailed('Недействительный refresh token.')
        if str(token['user_id']) != str(request.user.pk):
            raise PermissionDenied
        token.blacklist()
        return Response({'status': 'logged_out'})


class SwitchRoleAPI(BaseAPI):
    def post(self, request):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        return Response(token_pair(request.user, request.data.get('role')))


class MeAPI(BaseAPI):
    def get(self, request):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        return Response({**json_profile(request.user.profile, True), 'active_role': role(request)})

    def patch(self, request):
        require_roles(request, 'athlete', 'coach', 'judge', 'federation')
        person = request.user.profile
        data = model_to_dict(person)
        data.update(first_name=person.user.first_name, last_name=person.user.last_name)
        data['disciplines'] = list(person.disciplines.values_list('pk', flat=True))
        data.update(payload_dict(request.data))
        person = form_valid(ProfileForm(data, instance=person)).save()
        record_audit(request, 'Изменён профиль', f'profile:{person.pk}')
        return Response(json_profile(person, True))


class ProfilesAPI(BaseAPI):
    def get(self, request, pk=None, kind='athlete'):
        require_roles(request, 'federation', 'athlete', 'coach', 'judge')
        if pk is not None:
            person = get_object_or_404(people_for(request).select_related('user', 'organization'), pk=pk)
            return Response(json_profile(person, True))
        qs = role_profiles(kind)
        if request.query_params.get('q'):
            from django.db.models import Q
            q = request.query_params['q']
            qs = qs.filter(Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q))
        for query, field in [('discipline', 'disciplines'), ('organization', 'organization')]:
            if request.query_params.get(query, '').isdigit():
                qs = qs.filter(**{field: request.query_params[query]})
        rows, pager = self.page(request, qs.distinct())
        return pager.get_paginated_response([json_profile(p) for p in rows])

    def post(self, request, pk=None, kind='athlete'):
        require_roles(request, 'federation')
        if pk is not None:
            raise ValidationError('Создание выполняется через список участников.')
        user = form_valid(RegistrationForm(request.data)).save()
        record_audit(request, 'Создан профиль', f'profile:{user.profile.pk}')
        return Response(json_profile(user.profile, True), status=201)

    def patch(self, request, pk, kind='athlete'):
        require_roles(request, 'federation')
        person = get_object_or_404(Profile, pk=pk)
        data = model_to_dict(person)
        data.update(first_name=person.user.first_name, last_name=person.user.last_name)
        data['disciplines'] = list(person.disciplines.values_list('pk', flat=True))
        data.update(payload_dict(request.data))
        person = form_valid(FederationProfileForm(data, instance=person)).save()
        record_audit(request, 'Федерация изменила профиль', f'profile:{pk}')
        return Response(json_profile(person, True))


class CompetitionsAPI(BaseAPI):
    def get(self, request, pk=None):
        if isinstance(request.auth, PartnerToken):
            require_scope(request, 'read:competitions')
        qs = visible_competitions(request)
        if pk is not None:
            return Response(json_competition(get_object_or_404(qs, pk=pk)))
        if request.query_params.get('discipline', '').isdigit():
            qs = qs.filter(disciplines=request.query_params['discipline'])
        if request.query_params.get('state'):
            qs = qs.filter(state=request.query_params['state'])
        if request.query_params.get('q'):
            qs = qs.filter(title__icontains=request.query_params['q'])
        rows, pager = self.page(request, qs.distinct())
        return pager.get_paginated_response([json_competition(c) for c in rows])

    def post(self, request, pk=None):
        require_roles(request, 'federation')
        if pk is not None:
            raise ValidationError('Для изменения соревнования используйте PATCH.')
        comp = form_valid(CompetitionForm(request.data, request.FILES)).save()
        record_audit(request, 'Создано соревнование', f'competition:{comp.pk}', comp.title)
        return Response(json_competition(comp), status=201)

    @transaction.atomic
    def patch(self, request, pk):
        require_roles(request, 'federation')
        comp = get_object_or_404(Competition.objects.select_for_update(), pk=pk)
        data = model_to_dict(comp)
        data['disciplines'] = list(comp.disciplines.values_list('pk', flat=True))
        data.update(payload_dict(request.data))
        comp = form_valid(CompetitionForm(data, request.FILES, instance=comp)).save()
        record_audit(request, 'Изменено соревнование', f'competition:{pk}')
        return Response(json_competition(comp))


class CompetitionActionAPI(BaseAPI):
    def post(self, request, pk, action):
        require_roles(request, 'federation')
        bridge_request = copy(request)
        bridge_request._full_data = request.data.copy()
        bridge_request._full_data['action'] = {'open-applications': 'open', 'close-applications': 'close',
                                              'publish-protocol': 'publish', 'publish': 'publish_competition'}.get(action, action)
        bridge(bridge_request, workflow.competition_action, pk=pk)
        comp = get_object_or_404(Competition, pk=pk)
        expected = {'open-applications': 'open', 'close-applications': 'closed', 'publish-protocol': 'finished', 'publish': 'published'}
        if action in expected and comp.state != expected[action]:
            raise ValidationError('Переход не выполнен. Проверьте положение, даты, заявки и полноту результатов.')
        return Response(json_competition(comp))


class ApplicationsAPI(BaseAPI):
    def get(self, request, pk=None):
        require_roles(request, 'federation', 'athlete', 'coach')
        qs = Application.objects.all()
        if pk is not None:
            get_object_or_404(visible_competitions(request), pk=pk)
            qs = qs.filter(competition_id=pk)
        if role(request) != 'federation':
            qs = qs.filter(athlete__in=people_for(request))
        rows, pager = self.page(request, qs)
        return pager.get_paginated_response([json_application(a) for a in rows])

    def post(self, request, pk):
        require_roles(request, 'athlete', 'coach')
        bridge(request, workflow.application_create, pk=pk)
        app = get_object_or_404(Application, competition_id=pk, athlete_id=request.data.get('athlete'), discipline_id=request.data.get('discipline'))
        return Response(json_application(app), status=201)


class ModerateAPI(BaseAPI):
    def patch(self, request, pk):
        require_roles(request, 'federation')
        bridge(request, workflow.application_moderate, pk=pk)
        return Response(json_application(get_object_or_404(Application, pk=pk)))


class ResultsAPI(BaseAPI):
    def get(self, request, pk):
        if isinstance(request.auth, PartnerToken):
            require_scope(request, 'read:results')
        comp = get_object_or_404(visible_competitions(request), pk=pk)
        if not comp.protocol_published and role(request) != 'federation':
            raise PermissionDenied('Протокол ещё не опубликован.')
        rows = Result.objects.filter(application__competition=comp).select_related('application')
        return Response({'competition': pk, 'published': comp.protocol_published,
                         'results': [{'id': r.pk, 'application': r.application_id, 'athlete': r.application.athlete_id,
                                      'discipline': r.application.discipline_id, 'place': r.place, 'score': r.score} for r in rows]})

    def post(self, request, pk):
        require_roles(request, 'federation')
        bridge(request, workflow.result_save, pk=pk)
        result = get_object_or_404(Result, application_id=request.data.get('application'), application__competition_id=pk)
        return Response({'id': result.pk, 'application': result.application_id, 'place': result.place, 'score': result.score})


class ImportAPI(BaseAPI):
    def post(self, request, pk):
        require_scope(request, 'results:import')
        comp = get_object_or_404(Competition, pk=pk)
        rows = request.data.get('results')
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except ValueError:
                raise ValidationError('Некорректный JSON.')
        prepared = import_results(comp, rows, request.user)
        return Response({'imported': len(prepared)})


class QualificationsAPI(BaseAPI):
    def get(self, request, kind=None):
        require_roles(request, 'federation', 'athlete', 'coach', 'judge')
        qs = RankApplication.objects.filter(athlete__in=people_for(request))
        if kind:
            qs = qs.filter(kind=kind)
        if role(request) == 'judge':
            qs = qs.filter(kind='category')
        rows, pager = self.page(request, qs)
        return pager.get_paginated_response([json_qualification(r) for r in rows])

    def post(self, request, kind='rank'):
        require_roles(request, 'federation', 'athlete', 'coach', 'judge')
        data = request.data.copy()
        data['kind'] = kind
        form = form_valid(QualificationForm(data, user=request.user, role=role(request)))
        record = RankApplication(**form.cleaned_data, submitted_by=request.user)
        record.full_clean()
        record.save()
        record_audit(request, 'Запрошен разряд / категория', f'qualification:{record.pk}', record.requested_title)
        notify(record.athlete.user, 'Заявка на квалификацию отправлена', record.requested_title, '/qualifications/')
        return Response(json_qualification(record), status=201)


class QualificationReviewAPI(BaseAPI):
    def patch(self, request, pk):
        require_roles(request, 'federation')
        bridge(request, workflow.qualification_review, pk=pk)
        return Response(json_qualification(get_object_or_404(RankApplication, pk=pk)))


class JudgeAssignmentsAPI(BaseAPI):
    def get(self, request, pk=None):
        require_roles(request, 'federation', 'judge')
        qs = JudgeAssignment.objects.all()
        if role(request) == 'judge':
            qs = qs.filter(judge__user=request.user)
        if pk:
            qs = qs.filter(competition_id=pk)
        return Response([{'id': j.pk, 'competition': j.competition_id, 'judge': j.judge_id, 'position': j.position} for j in qs])

    def post(self, request, pk):
        require_roles(request, 'federation')
        bridge(request, workflow.judge_assign, pk=pk)
        return Response({'status': 'assigned'})


class RatingAPI(BaseAPI):
    def get(self, request):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        season, discipline, low, high = workflow.rating_filters(request)
        entries = calculate_rating(season, discipline, low, high, request.query_params.get('min_rank', ''))
        return Response({'season': season, 'discipline': discipline, 'results': [
            {'athlete': json_profile(e['athlete']), 'points': e['points'], 'coefficient': e['coefficient'],
             'breakdown': [{'competition': b['competition'].pk, 'place': b['place'], 'base': b['base']} for b in e['breakdown']]} for e in entries]})


class TeamsAPI(BaseAPI):
    def get(self, request, pk=None):
        require_roles(request, 'federation', 'ministry')
        qs = Team.objects.prefetch_related('members')
        def value(team):
            return {'id': team.pk, 'season': team.season, 'discipline': team.discipline_id,
                    'age_min': team.age_min, 'age_max': team.age_max, 'members': list(team.members.values_list('pk', flat=True))}
        if pk:
            return Response(value(get_object_or_404(qs, pk=pk)))
        rows, pager = self.page(request, qs)
        return pager.get_paginated_response([value(team) for team in rows])

    def post(self, request, pk=None):
        require_roles(request, 'federation')
        if pk is not None:
            raise ValidationError('Создание сборной выполняется через список.')
        response = bridge(request, workflow.team_create)
        return Response({'status': 'created', 'id': int(response['X-Created-Object'])}, status=201)


class CensusAPI(BaseAPI):
    def get(self, request):
        require_roles(request, 'federation', 'ministry')
        return Response({'results': census(*workflow.census_filters(request))})


class NotificationsAPI(BaseAPI):
    def get(self, request, pk=None):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        queryset = Notification.objects.filter(user=request.user)
        if pk is not None:
            queryset = queryset.filter(pk=pk)
        rows, pager = self.page(request, queryset)
        return pager.get_paginated_response([{'id': n.pk, 'title': n.title, 'body': n.body, 'url': n.url,
                                             'read': n.read, 'created_at': n.created_at} for n in rows])

    def patch(self, request, pk):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        notice = get_object_or_404(Notification, pk=pk, user=request.user)
        notice.read = True
        notice.save(update_fields=['read'])
        return Response({'id': pk, 'read': True})


class AuditAPI(BaseAPI):
    def get(self, request):
        require_roles(request, 'federation')
        rows, pager = self.page(request, AuditLog.objects.all())
        return pager.get_paginated_response([{'id': a.pk, 'user': a.user_id, 'role': a.role, 'action': a.action,
                                             'entity': a.entity, 'detail': a.detail, 'created_at': a.created_at} for a in rows])


class DictionariesAPI(BaseAPI):
    def get(self, request, kind='disciplines'):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        mapping = {'disciplines': Discipline, 'organizations': Organization, 'rating-rules': RatingRule,
                   'entries': DictionaryEntry, 'document-layouts': DocumentLayout}
        if kind not in mapping:
            raise ValidationError('Неизвестный справочник.')
        return Response(list(mapping[kind].objects.values()))

    def post(self, request, kind):
        require_roles(request, 'federation')
        if kind == 'organizations':
            record = form_valid(OrganizationForm(request.data)).save()
        elif kind == 'disciplines':
            from django.forms import modelform_factory
            cls = modelform_factory(Discipline, fields=['name', 'code'])
            record = form_valid(cls(request.data)).save()
        elif kind in ('entries', 'document-layouts'):
            from django.forms import modelform_factory
            model = DictionaryEntry if kind == 'entries' else DocumentLayout
            cls = modelform_factory(model, fields='__all__')
            record = form_valid(cls(request.data)).save()
        else:
            raise ValidationError('Для редактирования баллов используйте интерфейс федерации.')
        record_audit(request, 'Изменён справочник', f'{kind}:{record.pk}')
        return Response({'id': record.pk}, status=201)


class CoachLinksAPI(BaseAPI):
    def get(self, request, pk=None):
        require_roles(request, 'coach', 'athlete', 'federation')
        qs = CoachLink.objects.all()
        if role(request) == 'coach':
            qs = qs.filter(coach__user=request.user)
        elif role(request) == 'athlete':
            qs = qs.filter(athlete__user=request.user)
        rows, pager = self.page(request, qs)
        return pager.get_paginated_response([{'id': link.pk, 'coach': link.coach_id, 'athlete': link.athlete_id,
                                             'discipline': link.discipline_id, 'status': link.status} for link in rows])

    def post(self, request):
        require_roles(request, 'coach')
        bridge(request, workflow.coaching)
        link = get_object_or_404(CoachLink, coach__user=request.user, athlete_id=request.data.get('athlete'),
                                 discipline_id=request.data.get('discipline'))
        return Response({'id': link.pk, 'status': link.status}, status=201)

    def patch(self, request, pk):
        require_roles(request, 'coach', 'athlete', 'federation')
        bridge(request, workflow.link_update, pk=pk)
        return Response({'id': pk, 'status': CoachLink.objects.get(pk=pk).status})


class DocumentsAPI(BaseAPI):
    def get(self, request, kind, pk, fmt='json'):
        if isinstance(request.auth, PartnerToken):
            raise PermissionDenied
        if fmt == 'json' and kind == 'athlete':
            person = get_object_or_404(people_for(request), pk=pk)
            if role(request) not in ('federation', 'coach', 'athlete'):
                raise PermissionDenied
            return Response({'athlete': pk, 'results': [{'competition': r.application.competition_id,
                  'discipline': r.application.discipline_id, 'place': r.place, 'score': r.score} for r in workflow.results_for(person)]})
        if fmt not in ('xlsx', 'docx', 'pdf'):
            raise ValidationError('Поддерживаются xlsx, docx и pdf.')
        http = copy(request._request)
        http.user, http.active_role = request.user, role(request)
        return workflow.export_document(http, kind, pk, fmt)


class SchemaAPI(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        paths = {}
        endpoints = {'auth/login/': ['post'], 'auth/register/': ['post'], 'auth/refresh/': ['post'],
                     'auth/logout/': ['post'], 'auth/switch-role/': ['post'], 'me/': ['get', 'patch'],
                     'competitions/': ['get', 'post'], 'competitions/{id}/': ['get', 'patch'],
                     'competitions/{id}/applications/': ['post'], 'competitions/{id}/results/': ['get', 'post'],
                     'competitions/{id}/results/import/': ['post'], 'applications/': ['get'],
                     'applications/{id}/moderate/': ['patch'], 'rank-applications/': ['post'],
                     'qualifications/': ['get'], 'rating/': ['get'], 'national-team/': ['get', 'post'],
                     'reports/athletes-count/': ['get'], 'reports/athlete-competitions/{id}/': ['get'],
                     'notifications/': ['get'], 'audit-log/': ['get'], 'coach-links/': ['get', 'post'],
                     'coach-links/{id}/': ['patch'], 'competitions/{id}/publish/': ['post'],
                     'competitions/{id}/open-applications/': ['post'], 'competitions/{id}/close-applications/': ['post'],
                     'competitions/{id}/publish-protocol/': ['post'], 'qualifications/{id}/review/': ['patch'],
                     'judge-category-applications/': ['post'], 'competitions/{id}/judge-assignments/': ['get', 'post']}
        for endpoint, methods in endpoints.items():
            paths['/api/v1/' + endpoint] = {method: {'responses': {'200': {'description': 'Успех'},
                  '400': {'description': 'Ошибка валидации'}, '403': {'description': 'Нет разрешения'}},
                  'security': [] if endpoint.startswith('auth/') and endpoint != 'auth/switch-role/' else [{'JWT': []}]} for method in methods}
            for method in methods:
                operation = paths['/api/v1/' + endpoint][method]
                if '{id}' in endpoint:
                    operation['parameters'] = [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}]
                if method in ('post', 'patch'):
                    operation['requestBody'] = {'content': {'application/json': {'schema': {'type': 'object'}}}}
        return Response({'openapi': '3.0.3', 'info': {'title': 'ОренКод API', 'version': '1.0'}, 'paths': paths,
                         'components': {'securitySchemes': {'JWT': {'type': 'http', 'scheme': 'bearer', 'bearerFormat': 'JWT'},
                                                           'Partner': {'type': 'apiKey', 'in': 'header', 'name': 'Authorization'}}}})
