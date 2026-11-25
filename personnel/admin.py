from django.contrib import admin
from .models import EmployeeDocument, EmployeeDocumentAccess, EmployeeEmploymentHistory, EmployeeProfile, RosterAssignment, RosterAuditEvent, ShiftDefinition, WeeklyRoster


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


@admin.register(EmployeeEmploymentHistory)
class EmployeeEmploymentHistoryAdmin(admin.ModelAdmin):
    list_display = ("employee", "event_type", "effective_date", "recorded_by", "created_at")
    list_filter = ("event_type", "effective_date")
    readonly_fields = (
        "employee", "event_type", "effective_date", "previous_values",
        "new_values", "reason", "recorded_by", "created_at",
    )
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ("employee", "title", "category", "uploaded_by", "uploaded_at")
    list_filter = ("category", "uploaded_at")
    readonly_fields = ("uploaded_by", "uploaded_at", "original_filename")
    exclude = ("file",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmployeeDocumentAccess)
class EmployeeDocumentAccessAdmin(admin.ModelAdmin):
    list_display = ("document", "accessed_by", "action", "accessed_at")
    readonly_fields = ("document", "accessed_by", "action", "accessed_at")
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
