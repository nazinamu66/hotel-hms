from django import forms

from billing.models import Company, GroupReservation


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name",
            "registration_number",
            "contact_person",
            "phone",
            "email",
            "billing_address",
            "tax_number",
            "notes",
            "is_active",
        ]
        widgets = {
            "billing_address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class GroupReservationForm(forms.ModelForm):
    class Meta:
        model = GroupReservation
        fields = [
            "company",
            "name",
            "check_in_date",
            "check_out_date",
            "room_billing_party",
            "agreement_notes",
        ]
        widgets = {
            "check_in_date": forms.DateInput(attrs={"type": "date"}),
            "check_out_date": forms.DateInput(attrs={"type": "date"}),
            "agreement_notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(
            hotel=hotel, is_active=True
        ).order_by("name") if hotel else Company.objects.none()
