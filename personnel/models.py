from datetime import datetime, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ShiftDefinition(models.Model):
    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.CASCADE, related_name="personnel_shifts")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time", "name"]
        constraints = [models.UniqueConstraint(fields=["hotel", "code"], name="uniq_personnel_shift_code_hotel")]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    @property
    def crosses_midnight(self):
        return self.end_time <= self.start_time

    def interval_for(self, work_date):
        start = timezone.make_aware(datetime.combine(work_date, self.start_time))
        end_date = work_date + timedelta(days=1) if self.crosses_midnight else work_date
        end = timezone.make_aware(datetime.combine(end_date, self.end_time))
        return start, end

    def clean(self):
        if self.hotel_id is None:
            raise ValidationError({"hotel": "A shift must belong to a hotel."})


class WeeklyRoster(models.Model):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"), (SUBMITTED, "Submitted for review"),
        (APPROVED, "Approved"), (RETURNED, "Returned for changes"),
    ]

    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.PROTECT, related_name="weekly_rosters")
    department = models.ForeignKey("inventory.Department", on_delete=models.PROTECT, related_name="weekly_rosters")
    week_start = models.DateField(help_text="Monday of the roster week")
    responsible_hod = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="responsible_rosters")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_rosters")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_rosters")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_rosters")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start", "department__name"]
        constraints = [models.UniqueConstraint(fields=["hotel", "department", "week_start"], name="uniq_weekly_roster_department_week")]

    def __str__(self):
        return f"{self.department.name} · week of {self.week_start}"

    @property
    def week_end(self):
        return self.week_start + timedelta(days=6)

    def clean(self):
        errors = {}
        if self.week_start and self.week_start.weekday() != 0:
            errors["week_start"] = "Roster week must start on a Monday."
        if self.department_id and self.hotel_id and self.department.hotel_id != self.hotel_id:
            errors["department"] = "Department must belong to the selected hotel."
        if self.responsible_hod_id and self.department_id:
            hod = self.responsible_hod
            if not hod.is_active or hod.department_id != self.department_id or not hod.is_department_head:
                errors["responsible_hod"] = "Select an active Head of the roster department."
        if errors:
            raise ValidationError(errors)


class RosterAssignment(models.Model):
    roster = models.ForeignKey(WeeklyRoster, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="roster_assignments")
    work_date = models.DateField()
    shift = models.ForeignKey(ShiftDefinition, on_delete=models.PROTECT, null=True, blank=True, related_name="assignments")
    is_day_off = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["work_date", "employee__first_name", "employee__username"]
        constraints = [models.UniqueConstraint(fields=["roster", "employee", "work_date"], name="uniq_roster_employee_workday")]

    def clean(self):
        errors = {}
        if self.roster_id and self.work_date:
            if not self.roster.week_start <= self.work_date <= self.roster.week_end:
                errors["work_date"] = "Assignment date must fall within the roster week."
        if self.roster_id and self.employee_id:
            if self.employee.department_id != self.roster.department_id or self.employee.hotel_id != self.roster.hotel_id:
                errors["employee"] = "Employee must be active in this roster's department and hotel."
            if not self.employee.is_active:
                errors["employee"] = "Inactive employees cannot be rostered."
        if self.is_day_off and self.shift_id:
            errors["shift"] = "A day-off entry cannot also have a shift."
        if not self.is_day_off and not self.shift_id:
            errors["shift"] = "Choose a shift or mark the employee as off."
        if self.shift_id and self.roster_id and self.shift.hotel_id != self.roster.hotel_id:
            errors["shift"] = "Shift must belong to the roster hotel."
        if errors:
            raise ValidationError(errors)


class RosterAuditEvent(models.Model):
    roster = models.ForeignKey(WeeklyRoster, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="roster_audit_events")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.roster} · {self.event_type}"
