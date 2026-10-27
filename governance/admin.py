from django.contrib import admin
from .models import Appeal, AwardPetition, AwardRule, ProtocolAmendment, ViolationRecord

admin.site.register([Appeal, AwardPetition, AwardRule, ProtocolAmendment, ViolationRecord])
