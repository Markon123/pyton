from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import CoachLink, Profile, Role


def active_role(request):
    if not request.user.is_authenticated:
        return None
    try:
        roles = request.user.profile.roles
    except Profile.DoesNotExist:
        return None
    explicit = getattr(request, 'active_role', None)
    if explicit in roles:
        return explicit
    selected = request.session.get('active_role')
    return selected if selected in roles else (roles[0] if roles else None)


def roles_required(*roles):
    def decorator(view):
        @login_required
        @wraps(view)
        def checked(request, *args, **kwargs):
            if active_role(request) not in roles:
                raise PermissionDenied('В активной роли это действие недоступно.')
            return view(request, *args, **kwargs)
        return checked
    return decorator


def role_profiles(role):
    # JSON contains is not supported by SQLite. Use a portable role filter.
    ids = [p.pk for p in Profile.objects.only('pk', 'roles') if role in p.roles]
    return Profile.objects.filter(pk__in=ids).select_related('user', 'organization')


def managed_profiles(request):
    role = active_role(request)
    if role == Role.MINISTRY:
        return Profile.objects.none()
    if role == Role.FEDERATION:
        return Profile.objects.all()
    if role == Role.COACH:
        return Profile.objects.filter(coach_links__coach__user=request.user, coach_links__status='active').distinct()
    return Profile.objects.filter(user=request.user)


def can_view_person(request, person):
    return active_role(request) == Role.FEDERATION or (
        active_role(request) == Role.ATHLETE and person.user_id == request.user.pk
    ) or (active_role(request) == Role.JUDGE and person.user_id == request.user.pk) or (
        active_role(request) == Role.COACH and CoachLink.objects.filter(coach__user=request.user, athlete=person, status='active').exists()
    )
