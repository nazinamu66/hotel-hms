from datetime import timedelta

from django import forms
from django.utils import timezone

from personnel.models import RosterAssignment, WeeklyRoster
from .models import StaffAttendance


class StaffAttendanceArrivalForm(forms.ModelForm):
    assignment = forms.ModelChoiceField(
        queryset=RosterAssignment.objects.none(),
        label="Scheduled employee",
        empty_label="Select employee and approved shift",
        help_text="Only employees with an approved working shift are listed.",
    )

    class Meta:
        model = StaffAttendance
        fields = ["arrival_note"]
        widgets = {"arrival_note": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"})}

    def __init__(self, *args, hotel=None, now=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not hotel:
            return

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
            # Today's scheduled shifts remain selectable even after their
            # scheduled end so a very late arrival can still be recorded.
            # Yesterday's assignment is eligible only when it is an overnight
            # shift whose scheduled end has not yet passed.
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
