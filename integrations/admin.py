from django.contrib import admin
from .models import PartnerToken, WebhookEndpoint, IntegrationEvent, WebhookDelivery
for model in [PartnerToken, WebhookEndpoint, IntegrationEvent, WebhookDelivery]:
    admin.site.register(model)
