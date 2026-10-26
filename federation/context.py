from .access import active_role, managed_profiles
from .models import Role, Notification


def navigation(request):
    role = active_role(request)
    roles = getattr(getattr(request.user, 'profile', None), 'roles', []) if request.user.is_authenticated else []
    current = request.resolver_match.url_name if request.resolver_match else ''
    nav = current
    if current.startswith(('competition_', 'application_', 'result_', 'judge_', 'regulation_')):
        nav = 'competitions'
    elif current in ('athlete_detail', 'profile_create', 'profile_edit', 'organization_create'):
        nav = 'registry'
    labels = {'dashboard': 'Обзор', 'competitions': 'Соревнования', 'registry': 'Единый реестр',
              'qualifications': 'Разряды и категории', 'rating': 'Рейтинг и сборная',
              'reports': 'Отчёты и документы', 'coaching': 'Тренеры и спортсмены',
              'notifications': 'Уведомления', 'audit': 'Журнал действий', 'profile': 'Мой профиль', 'guide': 'С чего начать'}
    return {
        'active_role': role,
        'role_label': dict(Role.choices).get(role, ''),
        'available_roles': [(v, k) for v, k in Role.choices if v in roles],
        'unread_count': Notification.objects.filter(user=request.user, read=False).count() if request.user.is_authenticated else 0,
        'nav': nav,
        'page_title': labels.get(nav, 'Личный кабинет'),
        'accessible_ids': list(managed_profiles(request).values_list('pk', flat=True)) if request.user.is_authenticated and role else [],
    }
