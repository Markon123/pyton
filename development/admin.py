from django.contrib import admin
from .models import Evidence, ParticipationCertificate, RankCriterion

admin.site.register([Evidence, ParticipationCertificate, RankCriterion])
