from django.contrib import admin
from .models import EmployeeProfile, RosterAssignment, RosterAuditEvent, ShiftDefinition, WeeklyRoster


class RosterAssignmentInline(admin.TabularInline):
    model = RosterAssignment
    extra = 0


@admin.register(WeeklyRoster)
class WeeklyRosterAdmin(admin.ModelAdmin):
    list_display = ("hotel", "department", "week_start", "status", "responsible_hod", "reviewed_by")
    list_filter = ("hotel", "status", "department")
    inlines = [RosterAssignmentInline]


admin.site.register(ShiftDefinition)
admin.site.register(RosterAuditEvent)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ("employee_number", "first_name", "last_name", "hotel", "department", "job_title", "employment_status")
    list_filter = ("hotel", "department", "employment_status", "employment_type")
    search_fields = ("employee_number", "first_name", "last_name", "phone", "personal_email")
    readonly_fields = ("employee_number", "created_at", "updated_at")
