from django.urls import path
from . import views

app_name = 'training'
urlpatterns = [
    path('', views.index, name='index'),
    path('programs/new/', views.program_create, name='program_create'),
    path('programs/<int:pk>/edit/', views.program_create, name='program_edit'),
    path('groups/new/', views.group_create, name='group_create'),
    path('groups/<int:pk>/', views.group_detail, name='group_detail'),
    path('groups/<int:pk>/edit/', views.group_create, name='group_edit'),
    path('groups/<int:pk>/enroll/', views.enrollment_create, name='enroll'),
    path('enrollments/<int:pk>/end/', views.enrollment_end, name='enrollment_end'),
    path('enrollments/<int:pk>/info/', views.enrollment_info, name='enrollment_info'),
    path('enrollments/<int:pk>/transfer/', views.transfer, name='transfer'),
    path('groups/<int:pk>/sessions/new/', views.session_create, name='session_create'),
    path('sessions/<int:pk>/', views.session_detail, name='session_detail'),
    path('sessions/<int:pk>/edit/', views.session_edit, name='session_edit'),
    path('norms/', views.norms, name='norms'),
    path('norms/new/', views.norm_create, name='norm_create'),
    path('norms/<int:pk>/edit/', views.norm_create, name='norm_edit'),
    path('groups/<int:pk>/assessments/new/', views.assessment_create, name='assessment_create'),
]
