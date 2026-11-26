from django import forms

from .models import GymMember, GymMembershipPlan


class GymMemberForm(forms.ModelForm):
    class Meta:
        model = GymMember
        fields = (
            "first_name", "last_name", "phone", "email", "gender",
            "date_of_birth", "address", "photograph",
            "emergency_contact_name", "emergency_contact_phone", "notes",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class GymMembershipPlanForm(forms.ModelForm):
    class Meta:
        model = GymMembershipPlan
        fields = ("package", "price", "is_active")
        widgets = {"price": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}
