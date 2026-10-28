from django.contrib import admin
from django.urls import include, path

urlpatterns = [path('admin/', admin.site.urls), path('', include('federation.urls')),
    path('', include('identity.provider_urls', namespace='oauth2_provider')),
    path('identity/', include('identity.urls')),
    path('api/v1/', include('integrations.api_urls')),
    path('integrations/', include('integrations.urls')),
    path('training/', include('training.urls')),
    path('governance/', include('governance.urls')),
    path('development/', include('development.urls')),
    path('reporting/', include('reporting.urls')),
    path('privacy/', include('privacy_requests.urls')),
]
