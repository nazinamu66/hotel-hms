from django.contrib import admin
from .models import SecurityShiftConfiguration, SecurityShiftHandover


@admin.register(SecurityShiftConfiguration)
class SecurityShiftConfigurationAdmin(admin.ModelAdmin):
    list_display = ("hotel", "handover_time", "officers_per_team", "cso_leaves_at", "cso_daily", "is_active")
    list_filter = ("is_active", "cso_daily")
    search_fields = ("hotel__name",)


@admin.register(SecurityShiftHandover)
class SecurityShiftHandoverAdmin(admin.ModelAdmin):
    list_display = ("hotel", "duty_date", "submitted_by", "acknowledged_by", "is_complete", "cso_reviewed_at")
    list_filter = ("hotel", "is_complete", "duty_date")
    readonly_fields = ("submitted_at", "acknowledged_at", "register_snapshot", "created_at")
    search_fields = ("hotel__name", "outgoing_notes", "outstanding_matters")
