from django import forms
from django.contrib.auth import get_user_model
from .models import SecurityShiftHandover

User = get_user_model()


class SecurityShiftHandoverForm(forms.ModelForm):
    class Meta:
        model = SecurityShiftHandover
        fields = [
            "duty_date", "outgoing_officer_1", "outgoing_officer_2",
            "incoming_officer_1", "incoming_officer_2",
            "outgoing_notes", "outstanding_matters",
        ]
        widgets = {
            "duty_date": forms.DateInput(attrs={"type": "date"}),
            "outgoing_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Shift summary, events and instructions"}),
            "outstanding_matters": forms.Textarea(attrs={"rows": 4, "placeholder": "Unresolved matters the incoming team must follow up"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hotel = hotel
        officers = User.objects.filter(
            role="SECURITY", is_active=True, hotel=hotel, is_department_head=False
        ).order_by("first_name", "last_name", "username") if hotel else User.objects.none()
        for field in ("outgoing_officer_1", "outgoing_officer_2", "incoming_officer_1", "incoming_officer_2"):
            self.fields[field].queryset = officers
            self.fields[field].empty_label = "Select officer"

    def clean(self):
        cleaned = super().clean()
        chosen = [cleaned.get(name) for name in (
            "outgoing_officer_1", "outgoing_officer_2", "incoming_officer_1", "incoming_officer_2"
        )]
        chosen = [u.pk for u in chosen if u]
        if len(chosen) != len(set(chosen)):
            raise forms.ValidationError("Select four different officers for the outgoing and incoming teams.")
        return cleaned


class SecurityHandoverAcknowledgementForm(forms.Form):
    incoming_notes = forms.CharField(
        required=False, label="Incoming team notes",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes from the incoming team"}),
    )


class SecurityHandoverCSOReviewForm(forms.Form):
    cso_review_note = forms.CharField(
        required=False, label="CSO review note",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional supervisory comments"}),
    )
