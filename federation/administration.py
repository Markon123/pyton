"""Institution accounts are provisioned only by the single ministry principal."""
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import active_role, roles_required
from .models import Consent, MinistryAuthority, Profile, ProvisionedAccount
from .services import audit


def is_ministry_principal(user):
    return user.is_authenticated and user.is_active and MinistryAuthority.objects.filter(pk=1, user=user).exists()


def principal_required(view):
    @roles_required('ministry')
    @wraps(view)
    def checked(request, *args, **kwargs):
        if not is_ministry_principal(request.user):
            raise PermissionDenied('Аккаунты федераций создаёт главный аккаунт министерства.')
        return view(request, *args, **kwargs)
    return checked


class FederationAccountForm(UserCreationForm):
    institution = forms.CharField(label='Название федерации', max_length=200)
    email = forms.EmailField(label='Рабочая электронная почта')

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('institution', 'username', 'email', 'password1', 'password2')


class ActivationForm(SetPasswordForm):
    personal_consent = forms.BooleanField(label='Даю согласие на обработку данных своей учётной записи (версия 1.0).')


def revoke_account_tokens(user):
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
    from oauth2_provider.models import get_access_token_model, get_refresh_token_model
    get_refresh_token_model().objects.filter(user=user).delete()
    get_access_token_model().objects.filter(user=user).delete()


@principal_required
def federation_accounts(request):
    form = FederationAccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            # Lock the authority rather than granting rights from a submitted role.
            MinistryAuthority.objects.select_for_update().get(pk=1, user=request.user)
            user = form.save(commit=False)
            user.first_name = form.cleaned_data['institution'][:150]
            user.save()
            Profile.objects.create(user=user, roles=['federation'])
            ProvisionedAccount.objects.create(user=user, kind='federation', institution=form.cleaned_data['institution'], created_by=request.user)
            audit(request, 'Министерство создало аккаунт федерации', f'profile:{user.profile.pk}', form.cleaned_data['institution'])
        messages.success(request, 'Аккаунт федерации создан. Передайте сотруднику логин и временный пароль защищённым способом. При первом входе он сменит пароль.')
        return redirect('federation_accounts')
    accounts = ProvisionedAccount.objects.filter(kind='federation').select_related('user', 'created_by')
    return render(request, 'federation/federation_accounts.html', {'form': form, 'accounts': accounts}, status=400 if request.method == 'POST' else 200)


@require_POST
@principal_required
@transaction.atomic
def federation_account_status(request, pk):
    account = get_object_or_404(ProvisionedAccount.objects.select_for_update().select_related('user'), pk=pk, kind='federation')
    status = request.POST.get('status')
    if status not in ('active', 'inactive'):
        return render(request, 'federation/form.html', {'form': forms.Form(), 'title': 'Некорректное состояние аккаунта'}, status=400)
    account.user.is_active = status == 'active'
    account.user.save(update_fields=['is_active'])
    if not account.user.is_active:
        revoke_account_tokens(account.user)
    audit(request, 'Изменён доступ аккаунта федерации', f'profile:{account.user.profile.pk}', status)
    messages.success(request, 'Доступ включён.' if account.user.is_active else 'Доступ отключён.')
    return redirect('federation_accounts')


@principal_required
def federation_account_reset(request, pk):
    account = get_object_or_404(ProvisionedAccount.objects.select_related('user'), pk=pk, kind='federation')
    form = SetPasswordForm(account.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            account = ProvisionedAccount.objects.select_for_update().get(pk=account.pk)
            form.save()
            account.must_change_password = True
            account.activated_at = None
            account.save(update_fields=['must_change_password', 'activated_at'])
            revoke_account_tokens(account.user)
            audit(request, 'Министерство сменило временный пароль федерации', f'profile:{account.user.profile.pk}')
        messages.success(request, 'Временный пароль установлен. При следующем входе сотрудник задаст свой пароль.')
        return redirect('federation_accounts')
    return render(request, 'federation/form.html', {'form': form, 'title': f'Временный пароль · {account.institution}', 'cancel_url': '/administration/federations/'}, status=400 if request.method == 'POST' else 200)


@roles_required('ministry', 'federation')
def account_activation(request):
    account = ProvisionedAccount.objects.filter(user=request.user, must_change_password=True).first()
    if not account:
        return redirect('dashboard')
    form = ActivationForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            account.must_change_password = False
            account.activated_at = timezone.now()
            account.save(update_fields=['must_change_password', 'activated_at'])
            Consent.objects.get_or_create(user=request.user, kind='personal_data', version='1.0')
            revoke_account_tokens(request.user)
            audit(request, 'Служебный аккаунт активирован владельцем', f'profile:{request.user.profile.pk}', 'Пароль сменён; согласие подтверждено.')
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Первый вход завершён. Ваш новый пароль сохранён.')
        return redirect('dashboard')
    return render(request, 'federation/account_activation.html', {'form': form, 'account': account}, status=400 if request.method == 'POST' else 200)
