from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from federation.access import role_profiles
from federation.models import AuditLog, Consent, Profile


class Command(BaseCommand):
    help = 'Создать первую бизнес-учётную запись федерации без демонстрационных данных.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--email', required=True)

    @transaction.atomic
    def handle(self, **options):
        User = get_user_model()
        if role_profiles('federation').filter(user__is_active=True).exists():
            raise CommandError('Действующий кабинет федерации уже существует. Команда предназначена для первоначального запуска.')
        if User.objects.filter(username=options['username']).exists():
            raise CommandError('Имя пользователя занято. Существующим аккаунтам права не изменяются.')
        from django.core.validators import validate_email
        try:
            validate_email(options['email'])
        except ValidationError:
            raise CommandError('Укажите корректный email.')
        password = getpass('Пароль первого сотрудника федерации: ')
        if password != getpass('Повторите пароль: '):
            raise CommandError('Пароли не совпадают.')
        if input('Подтверждаете согласие на обработку данных своей учётной записи (версия 1.0)? Введите «да»: ').strip().lower() != 'да':
            raise CommandError('Для создания учётной записи требуется согласие.')
        user = User(username=options['username'], email=options['email'])
        try:
            user.full_clean(exclude=['password'])
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError(' '.join(error.messages))
        user.set_password(password)
        user.save()
        profile = Profile.objects.create(user=user, roles=['federation'])
        Consent.objects.create(user=user, kind='personal_data')
        AuditLog.objects.create(user=user, role='federation', action='Первоначальная настройка федерации', entity=f'profile:{profile.pk}')
        self.stdout.write('Первый кабинет федерации создан. Пароль не выводится; технические права Django не выдаются.')
