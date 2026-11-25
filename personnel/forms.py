from django import forms
from django.contrib.auth import get_user_model
from inventory.models import Department
from .models import RosterReconciliation, ShiftDefinition, WeeklyRoster
from .permissions import is_hr_head, manageable_roster_departments

User = get_user_model()


class WeeklyRosterCreateForm(forms.ModelForm):
    rest_days_per_employee = forms.IntegerField(min_value=0, max_value=6, initial=1, help_text="Set the number of OFF days each employee receives for this specific week. This does not change other weeks.")
    class Meta:
        model = WeeklyRoster
        fields = ["department", "week_start", "rest_days_per_employee", "responsible_hod"]
        widgets = {"week_start": forms.DateInput(attrs={"type": "date"}), "rest_days_per_employee": forms.NumberInput(attrs={"min": 0, "max": 6})}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        departments = manageable_roster_departments(actor)
        self.fields["department"].queryset = departments.select_related("hotel")
        self.fields["responsible_hod"].queryset = User.objects.filter(is_active=True, is_department_head=True, department__in=departments).select_related("department").order_by("department__name", "username")
        if actor and not is_hr_head(actor) and actor.department_id:
            self.initial.setdefault("department", actor.department_id)
            self.fields["department"].widget = forms.HiddenInput()
            self.fields["responsible_hod"].queryset = self.fields["responsible_hod"].queryset.filter(pk=actor.pk)
            self.initial.setdefault("responsible_hod", actor.pk)

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get("department")
        hod = cleaned.get("responsible_hod")
        if department and hod and hod.department_id != department.pk:
            self.add_error("responsible_hod", "The responsible HOD must belong to the selected department.")
        if department and self.actor and not manageable_roster_departments(self.actor).filter(pk=department.pk).exists():
            self.add_error("department", "You cannot prepare a roster for this department.")
        return cleaned


class ShiftDefinitionForm(forms.ModelForm):
    class Meta:
        model = ShiftDefinition
        fields = ["department", "code", "name", "start_time", "end_time", "grace_minutes", "is_active"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}), "end_time": forms.TimeInput(attrs={"type": "time"})}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["department"].queryset = manageable_roster_departments(actor).select_related("hotel")
        self.fields["department"].required = True

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get("department")
        if department:
            if not manageable_roster_departments(self.actor).filter(pk=department.pk).exists():
                self.add_error("department", "You cannot configure shifts for this department.")
            self.instance.hotel = department.hotel
        return cleaned


class RosterReconciliationForm(forms.ModelForm):
    class Meta:
        model = RosterReconciliation
        fields = ["reason", "covered_employee", "actual_shift_name", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Explain the arrangement and any relevant details."}),
            "actual_shift_name": forms.TextInput(attrs={"placeholder": "e.g. Morning shift (08:00–15:00)"}),
        }
        help_texts = {
            "covered_employee": "For a shift swap or cover, select the employee whose shift was covered.",
            "actual_shift_name": "Optional; record the shift actually worked if known.",
        }

    def __init__(self, *args, reconciliation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = True
        self.fields["covered_employee"].queryset = User.objects.filter(
            is_active=True, department_id=reconciliation.employee.department_id
        ).exclude(pk=reconciliation.employee_id).order_by("first_name", "last_name", "username") if reconciliation else User.objects.none()
