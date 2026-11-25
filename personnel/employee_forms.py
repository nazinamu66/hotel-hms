from django import forms
from django.utils import timezone
from accounts.services.access import get_accessible_hotels
from inventory.models import Department
from .models import EmployeeProfile


class EmployeeProfileForm(forms.ModelForm):
    effective_date = forms.DateField(
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Date the recorded employment changes take effect.",
    )
    change_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Reason for the employment changes (optional).",
    )
    class Meta:
        model = EmployeeProfile
        fields = [
            "hotel", "department", "first_name", "middle_name", "last_name",
            "preferred_name", "personal_email", "phone", "address",
            "job_title", "employment_type", "employment_status",
            "hire_date", "end_date", "hr_notes",
        ]
        widgets = {
            "hire_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 3}),
            "hr_notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        hotels = get_accessible_hotels(actor) if actor else get_accessible_hotels(None)
        self.fields["hotel"].queryset = hotels.order_by("name")
        self.fields["department"].queryset = Department.objects.filter(
            hotel__in=hotels, is_active=True,
        ).select_related("hotel").order_by("hotel__name", "name")
        if actor and is_hr_scoped(actor):
            hotel_id = actor.department.hotel_id
            self.fields["hotel"].queryset = self.fields["hotel"].queryset.filter(pk=hotel_id)
            self.fields["hotel"].initial = hotel_id
            self.fields["hotel"].disabled = True
            self.fields["department"].queryset = self.fields["department"].queryset.filter(hotel_id=hotel_id)

    def clean(self):
        cleaned = super().clean()
        hotel = cleaned.get("hotel")
        department = cleaned.get("department")
        if hotel and department and department.hotel_id != hotel.pk:
            self.add_error("department", "Choose a department belonging to the selected hotel.")
        return cleaned


def is_hr_scoped(user):
    return bool(user and user.role == "HR" and user.department_id)
