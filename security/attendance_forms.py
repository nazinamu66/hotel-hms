from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from personnel.models import RosterAssignment, WeeklyRoster
from .models import StaffAttendance

User = get_user_model()


class StaffAttendanceArrivalForm(forms.ModelForm):
    employee = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Employee",
        empty_label="Select any active hotel employee",
    )
    assignment = forms.ModelChoiceField(
        queryset=RosterAssignment.objects.none(),
        required=False,
        label="Scheduled shift (optional)",
        empty_label="Not working the published shift / emergency cover",
        help_text=(
            "Link the published shift only when the employee is working that assignment. "
            "Leave this blank for a shift swap, emergency cover or an unscheduled visit."
        ),
    )

    class Meta:
        model = StaffAttendance
        fields = ["arrival_note"]
        widgets = {"arrival_note": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note, e.g. covering another employee"})}

    def __init__(self, *args, hotel=None, now=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not hotel:
            return

        employee_hotel = Q(hotel=hotel) | Q(department__hotel=hotel)
        self.fields["employee"].queryset = User.objects.filter(
            employee_hotel, is_active=True,
        ).exclude(
            role__in={"ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT"}
        ).distinct().order_by("first_name", "last_name", "username")

        now = now or timezone.now()
        today = timezone.localdate(now)
        yesterday = today - timedelta(days=1)
        candidates = RosterAssignment.objects.filter(
            roster__hotel=hotel,
            roster__status=WeeklyRoster.APPROVED,
            work_date__in=[today, yesterday],
            is_day_off=False,
            shift__isnull=False,
        ).select_related(
            "employee", "employee__department", "shift", "roster", "roster__department",
        )
        eligible_ids = []
        for assignment in candidates:
            _start, end = assignment.shift.interval_for(assignment.work_date)
            if assignment.work_date == today or (
                assignment.shift.crosses_midnight and now <= end
            ):
                eligible_ids.append(assignment.pk)

        self.fields["assignment"].queryset = candidates.model.objects.filter(
            pk__in=eligible_ids
        ).select_related(
            "employee", "employee__department", "shift", "roster", "roster__department",
        ).order_by("work_date", "employee__first_name", "employee__last_name")

        self.fields["assignment"].label_from_instance = lambda assignment: (
            f"{assignment.employee.get_full_name() or assignment.employee.username}"
            f" · {assignment.roster.department.name}"
            f" · {assignment.shift.name}"
            f" · {assignment.work_date:%d %b}"
            f" ({assignment.shift.start_time:%H:%M}–{assignment.shift.end_time:%H:%M})"
        )

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        assignment = cleaned.get("assignment")
        if assignment and employee and assignment.employee_id != employee.pk:
            self.add_error("assignment", "Choose a shift assigned to the selected employee, or leave this field blank for emergency cover.")
        return cleaned
