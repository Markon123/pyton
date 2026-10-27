from django.contrib import admin
from .models import (OrganizationProgram, TrainingGroup, Enrollment, Session, Attendance,
                     AssessmentNorm, AssessmentResult, Attestation)

for model in (OrganizationProgram, TrainingGroup, Enrollment, Session, Attendance, AssessmentNorm, AssessmentResult, Attestation):
    admin.site.register(model)
