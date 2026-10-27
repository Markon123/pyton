"""Mount at the URL root with namespace oauth2_provider. Management uses our role checks."""
from django.urls import path
from oauth2_provider import views

from .views import ConsentView

app_name = 'oauth2_provider'
urlpatterns = [
    path('.well-known/openid-configuration', views.ConnectDiscoveryInfoView.as_view(), name='oidc-connect-discovery-info'),
    path('.well-known/jwks.json', views.JwksInfoView.as_view(), name='jwks-info'),
    path('.well-known/oauth-authorization-server', views.OAuthServerMetadataView.as_view(), name='oauth-server-metadata'),
    path('o/authorize/', ConsentView.as_view(), name='authorize'),
    path('o/token/', views.TokenView.as_view(), name='token'),
    path('o/userinfo/', views.UserInfoView.as_view(), name='user-info'),
    path('o/revoke/', views.RevokeTokenView.as_view(), name='revoke-token'),
]
