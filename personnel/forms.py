from django import forms
from django.contrib.auth import get_user_model
from inventory.models import Department
from .models import ShiftDefinition, WeeklyRoster
from .permissions import is_hr_head, manageable_roster_departments

User = get_user_model()


class WeeklyRosterCreateForm(forms.ModelForm):
    class Meta:
        model = WeeklyRoster
        fields = ["department", "week_start", "responsible_hod"]
        widgets = {"week_start": forms.DateInput(attrs={"type": "date"})}

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
        fields = ["code", "name", "start_time", "end_time", "grace_minutes", "is_active"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}), "end_time": forms.TimeInput(attrs={"type": "time"})}
