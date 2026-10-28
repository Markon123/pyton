"""Create the single ministry principal without a published/default password."""
import os
from pathlib import Path
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from federation.models import AuditLog, MinistryAuthority, Profile, ProvisionedAccount


class Command(BaseCommand):
    help = 'Подготовить единственный главный аккаунт министерства. Повторный запуск пароль не меняет.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default=os.getenv('FSP_MINISTRY_USERNAME', 'ministry'))
        parser.add_argument('--email', default=os.getenv('FSP_MINISTRY_EMAIL', ''))
        parser.add_argument('--credentials-file', default=str(settings.LOCAL_DIR / 'first-access.txt'))

    def handle(self, **options):
        existing = MinistryAuthority.objects.select_related('user').filter(pk=1).first()
        if existing:
            self.stdout.write('Главный аккаунт министерства уже подготовлен. Логин и пароль сохранены без изменений.')
            return
        user_model = get_user_model()
        if user_model.objects.filter(username=options['username']).exists():
            raise CommandError('Логин занят существующим аккаунтом. Его пароль и права не изменены. Укажите другой FSP_MINISTRY_USERNAME для первоначальной настройки.')
        configured_password = os.getenv('FSP_MINISTRY_PASSWORD', '')
        password = configured_password or secrets.token_urlsafe(24)
        user = user_model(username=options['username'], email=options['email'], first_name='Министерство')
        try:
            user.full_clean(exclude=['password'])
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError(' '.join(error.messages))
        path = Path(options['credentials_file'])
        created_file = False
        try:
            with transaction.atomic():
                if not configured_password:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    created_file = True
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                        output.write('ОренКод · первоначальный вход министерства\n'
                                     f'Логин: {user.username}\nВременный пароль: {password}\n\n'
                                     'При первом входе задайте свой пароль. После смены этот временный пароль перестанет действовать.\n'
                                     'Файл предназначен только владельцу установки. Не публикуйте и не отправляйте его в чат.\n')
                user.set_password(password)
                user.save()
                Profile.objects.create(user=user, roles=['ministry'])
                MinistryAuthority.objects.create(pk=1, user=user)
                ProvisionedAccount.objects.create(user=user, kind='ministry', institution='Министерство физической культуры и спорта Оренбургской области')
                AuditLog.objects.create(user=user, role='ministry', action='Подготовлен главный аккаунт министерства', entity=f'profile:{user.profile.pk}')
        except Exception as error:
            if created_file:
                path.unlink(missing_ok=True)
            if isinstance(error, FileExistsError):
                raise CommandError('Файл первоначального доступа уже существует. Он не перезаписан. Проверьте выбранную базу или задайте другой --credentials-file.') from error
            raise
        if configured_password:
            self.stdout.write('Главный аккаунт министерства создан. Временный пароль взят из защищённых настроек; в вывод он не записывается.')
        else:
            self.stdout.write(f'Главный аккаунт министерства создан. Данные первого входа: {path}. Пароль не выводится в журнал.')
