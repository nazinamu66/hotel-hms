from django.contrib import admin
from .models import RosterAssignment, RosterAuditEvent, ShiftDefinition, WeeklyRoster


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
