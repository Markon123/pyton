from django.urls import path
from . import views

app_name = 'governance'
urlpatterns = [
    path('', views.appeals, name='index'),
    path('appeals/', views.appeals, name='appeals'),
    path('appeals/<int:pk>/', views.appeal_detail, name='appeal_detail'),
    path('violations/new/', views.violation_create, name='violation_create'),
    path('evidence/<str:kind>/<int:pk>/', views.evidence, name='evidence'),
    path('awards/', views.awards, name='awards'),
    path('awards/rules/new/', views.award_rule, name='award_rule_create'),
    path('awards/rules/<int:pk>/', views.award_rule, name='award_rule'),
    path('awards/prepare/', views.petition_prepare, name='petition_prepare'),
    path('awards/<int:pk>/', views.petition_detail, name='petition_detail'),
    path('awards/<int:pk>/document/<str:fmt>/', views.petition_document, name='petition_document'),
    path('ministry/', views.ministry, name='ministry'),
]
