from django.urls import path
from . import views

app_name = 'reporting'
urlpatterns = [path('', views.index, name='index'), path('new/', views.create, name='create'),
               path('<uuid:number>/', views.job, name='job'), path('<uuid:number>/download/', views.download, name='download')]
