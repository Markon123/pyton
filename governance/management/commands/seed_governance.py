from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from federation.models import Competition, Profile, Result
from governance.models import Appeal, AwardPetition, AwardRule
from governance.services import award_candidates


class Command(BaseCommand):
    help = 'Идемпотентное демонстрационное наполнение премий и кабинета министерства.'

    @transaction.atomic
    def handle(self, *args, **options):
        user, created = get_user_model().objects.get_or_create(username='ministry', defaults={'first_name': 'Демо', 'last_name': 'Министерство', 'email': 'ministry@example.test'})
        if created:
            user.set_password('FspDemo2026!')
            user.save()
        Profile.objects.get_or_create(user=user, defaults={'roles': ['ministry'], 'municipality': 'Оренбург'})
        for index, (level, _) in enumerate(Competition.LEVELS, 1):
            for place in (1, 2, 3):
                AwardRule.objects.get_or_create(level=level, place=place, defaults={'amount': index * (4 - place) * 10000, 'demonstration': True,
                    'source': 'ДЕМОНСТРАЦИЯ: суммы условные. Настроить по действующему указу Губернатора № 93-ук.'})
        federation = get_user_model().objects.filter(username='federation', email='federation@example.test').first()
        if federation:
            for item in award_candidates(timezone.localdate().year)[:2]:
                AwardPetition.objects.get_or_create(athlete=item['athlete'], year=timezone.localdate().year, defaults={
                    'result': item['result'], 'rule': item['rule'], 'amount': item['amount'], 'created_by': federation,
                    'provenance': {'policy': 'best_result', 'results': item['breakdown'], 'demonstration': item['rule'].demonstration}})
        athlete = get_user_model().objects.filter(username='athlete', email='athlete@example.test').first()
        if athlete:
            result = Result.objects.filter(application__athlete__user=athlete, application__competition__protocol_published=True).first()
            if result:
                Appeal.objects.get_or_create(result=result, submitted_by=athlete,
                    reason='ДЕМО: прошу сверить баллы с демонстрационным журналом проверки. Все сведения вымышлены.')
        self.stdout.write(self.style.SUCCESS('Готовы демо-правила премий и ministry / FspDemo2026! (только чтение). Существующие записи сохранены.'))
