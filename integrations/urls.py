from django.urls import path
from . import views
app_name = 'integrations'
urlpatterns = [path('', views.index, name='index'), path('tokens/<int:pk>/revoke/', views.revoke_token, name='revoke'),
               path('webhooks/<int:pk>/disable/', views.disable_endpoint, name='disable')]
