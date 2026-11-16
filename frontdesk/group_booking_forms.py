from django import forms

from billing.models import Company


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
