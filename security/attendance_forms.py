from django import forms
from django.contrib.auth import get_user_model
from .models import StaffAttendance

User = get_user_model()


class StaffAttendanceArrivalForm(forms.ModelForm):
    class Meta:
        model = StaffAttendance
        fields = ["employee", "arrival_note"]
        widgets = {"arrival_note": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"})}

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = User.objects.filter(hotel=hotel, is_active=True).exclude(
            role__in=["ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT"]
        ).select_related("department").order_by("department__name", "first_name", "last_name", "username")
        self.fields["employee"].label_from_instance = lambda user: (
            f"{user.get_full_name() or user.username} · {user.department.name if user.department_id else user.get_role_display()}"
        )
