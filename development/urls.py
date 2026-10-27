from django.urls import path
from . import views

app_name = 'development'
urlpatterns = [
    path('', views.index, name='index'),
    path('pathway/', views.pathway, name='pathway'),
    path('candidates/', views.candidates, name='candidates'),
    path('criteria/', views.criteria, name='criteria'),
    path('evidence/new/', views.evidence_create, name='evidence_create'),
    path('evidence/<int:pk>/', views.evidence_download, name='evidence_download'),
    path('certificates/', views.certificates, name='certificates'),
    path('certificates/issue/', views.certificate_issue, name='certificate_issue'),
    path('certificates/<uuid:number>/pdf/', views.certificate_download, name='certificate_download'),
    path('certificates/<uuid:number>/revoke/', views.certificate_revoke, name='certificate_revoke'),
    path('verify/<uuid:number>/', views.verify, name='verify'),
    path('judging/', views.judging, name='judging'),
    path('judging/<str:kind>/<int:pk>/<str:fmt>/', views.judging_document, name='judging_document'),
]
