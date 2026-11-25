from django import forms
from accounts.services.access import get_accessible_hotels
from .models import EmployeeProfile, PersonnelAction
from .permissions import is_hr_staff


class PersonnelActionForm(forms.ModelForm):
    class Meta:
        model = PersonnelAction
        fields = ["employee", "action_type", "title", "reason", "effective_date", "proposed_job_title", "proposed_department", "attachment"]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 4}),
            "effective_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        hotels = get_accessible_hotels(actor)
        self.fields["employee"].queryset = EmployeeProfile.objects.filter(hotel__in=hotels).select_related("hotel", "department")
        from inventory.models import Department
        self.fields["proposed_department"].queryset = Department.objects.filter(hotel__in=hotels, is_active=True).order_by("name")
        self.actor = actor

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        department = cleaned.get("proposed_department")
        if employee and department and department.hotel_id != employee.hotel_id:
            self.add_error("proposed_department", "Choose a department belonging to the employee's hotel.")
        return cleaned
