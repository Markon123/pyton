from django.urls import path

from . import views

app_name = 'privacy_requests'

urlpatterns = [
    path('', views.index, name='index'),
    path('export/', views.export, name='export'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/review/', views.review, name='review'),
]
