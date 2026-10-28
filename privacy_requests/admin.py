from django.contrib import admin

from .models import PrivacyRequest, PrivacyRequestEvent


class EventInline(admin.TabularInline):
    model = PrivacyRequestEvent
    extra = 0
    can_delete = False
    readonly_fields = ('actor', 'status', 'comment', 'created_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PrivacyRequest)
class PrivacyRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'created_at', 'reviewed_at')
    list_filter = ('status',)
    readonly_fields = tuple(field.name for field in PrivacyRequest._meta.fields)
    inlines = [EventInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
