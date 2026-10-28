from django.contrib import admin

from .models import (
    Application, AuditLog, CoachLink, Competition, Consent, Discipline,
    JudgeAssignment, Notification, Organization, Profile, RankApplication,
    RatingRule, ReportDeadline, Result, Team,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'municipality', 'organization', 'status']
    list_filter = ['status', 'organization', 'disciplines']
    search_fields = ['user__first_name', 'user__last_name', 'user__username']
    filter_horizontal = ['disciplines']


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ['title', 'starts_on', 'state', 'level']
    list_filter = ['state', 'level', 'disciplines']
    search_fields = ['title']
    filter_horizontal = ['disciplines']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'role', 'action', 'entity']
    list_filter = ['role']
    readonly_fields = ['user', 'role', 'action', 'entity', 'detail', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [Discipline, Organization, Application, Result, RankApplication, CoachLink, JudgeAssignment, Team, Notification, Consent, RatingRule]:
    admin.site.register(model)


@admin.register(ReportDeadline)
class ReportDeadlineAdmin(admin.ModelAdmin):
    list_display = ['title', 'due_on', 'active', 'completed_at']
    list_filter = ['active', 'due_on']
    filter_horizontal = ['recipients']
    search_fields = ['title', 'description']

admin.site.site_header = 'ФСП • Управление платформой'
admin.site.site_title = 'ФСП Оренбург'
admin.site.index_title = 'Справочники и данные'
