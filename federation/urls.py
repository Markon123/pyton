from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views
from .configuration import configuration
from .deadlines import deadline_list, deadline_complete
from .mail_status import mail_status
from .administration import (account_activation, federation_accounts,
                             federation_account_status, federation_account_reset)

urlpatterns = [
    path('configuration/', configuration, name='configuration'),
    path('login/', LoginView.as_view(template_name='federation/login.html'), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('account/activate/', account_activation, name='account_activation'),
    path('administration/federations/', federation_accounts, name='federation_accounts'),
    path('administration/federations/<int:pk>/status/', federation_account_status, name='federation_account_status'),
    path('administration/federations/<int:pk>/password/', federation_account_reset, name='federation_account_reset'),
    path('switch-role/', views.switch_role, name='switch_role'),
    path('', views.dashboard, name='dashboard'),
    path('competitions/', views.competitions, name='competitions'),
    path('competitions/new/', views.competition_create, name='competition_create'),
    path('competitions/<int:pk>/', views.competition_detail, name='competition_detail'),
    path('competitions/<int:pk>/edit/', views.competition_edit, name='competition_edit'),
    path('competitions/<int:pk>/action/', views.competition_action, name='competition_action'),
    path('competitions/<int:pk>/apply/', views.application_create, name='application_create'),
    path('applications/<int:pk>/moderate/', views.application_moderate, name='application_moderate'),
    path('competitions/<int:pk>/results/', views.result_save, name='result_save'),
    path('competitions/<int:pk>/judges/', views.judge_assign, name='judge_assign'),
    path('competitions/<int:pk>/regulation/', views.regulation_download, name='regulation_download'),
    path('registry/', views.registry, name='registry'),
    path('registry/new/', views.profile_create, name='profile_create'),
    path('registry/<int:pk>/', views.athlete_detail, name='athlete_detail'),
    path('registry/<int:pk>/edit/', views.profile_edit, name='profile_edit'),
    path('organizations/new/', views.organization_create, name='organization_create'),
    path('organizations/<int:pk>/', views.organization_detail, name='organization_detail'),
    path('report-deadlines/', deadline_list, name='report_deadlines'),
    path('report-deadlines/<int:pk>/complete/', deadline_complete, name='report_deadline_complete'),
    path('profile/', views.profile, name='profile'),
    path('qualifications/', views.qualifications, name='qualifications'),
    path('qualifications/<int:pk>/review/', views.qualification_review, name='qualification_review'),
    path('coaching/', views.coaching, name='coaching'),
    path('coaching/<int:pk>/status/', views.link_update, name='link_update'),
    path('rating/', views.rating, name='rating'),
    path('rating/rules/', views.rating_rules, name='rating_rules'),
    path('teams/new/', views.team_create, name='team_create'),
    path('reports/', views.reports, name='reports'),
    path('documents/<str:kind>/<int:pk>/<str:fmt>/', views.export_document, name='export_document'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/mail/', mail_status, name='mail_status'),
    path('audit/', views.audit_log, name='audit'),
    path('guide/', views.guide, name='guide'),
    path('health/', views.health, name='health'),
]
