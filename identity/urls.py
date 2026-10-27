from django.urls import path

from . import views

app_name = 'identity'
urlpatterns = [
    path('', views.clients, name='clients'),
    path('clients/new/', views.client_create, name='client_create'),
    path('clients/<int:pk>/edit/', views.client_edit, name='client_edit'),
    path('clients/<int:pk>/revoke/', views.client_revoke, name='client_revoke'),
    path('grants/', views.grants, name='grants'),
    path('grants/<int:pk>/revoke/', views.grant_revoke, name='grant_revoke'),
    path('demo/', views.demo, name='demo'),
    path('demo/start/', views.demo_start, name='demo_start'),
    path('demo/callback/', views.demo_callback, name='demo_callback'),
]
