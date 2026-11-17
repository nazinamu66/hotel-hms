from decimal import Decimal

from django import forms
from django.forms import formset_factory


class CompanyDocumentHeaderForm(forms.Form):
    document_type = forms.ChoiceField(
        choices=(("QUOTATION", "Quotation"), ("PROFORMA", "Proforma Invoice")),
        initial="QUOTATION",
    )
    issue_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    valid_until = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    terms = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Payment terms, validity, cancellation terms..."}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note to the corporate client"}),
    )

    def clean(self):
        cleaned = super().clean()
        issue_date = cleaned.get("issue_date")
        valid_until = cleaned.get("valid_until")
        if issue_date and valid_until and valid_until < issue_date:
            self.add_error("valid_until", "Validity date cannot be before the issue date.")
        return cleaned


class CompanyDocumentLineForm(forms.Form):
    description = forms.CharField(max_length=240, required=False)
    quantity = forms.DecimalField(
        required=False, min_value=Decimal("0.01"), max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    unit = forms.CharField(max_length=30, required=False)
    unit_price = forms.DecimalField(
        required=False, min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    def clean(self):
        cleaned = super().clean()
        description = (cleaned.get("description") or "").strip()
        if description:
            if cleaned.get("quantity") is None:
                self.add_error("quantity", "Enter a quantity for this item.")
            if cleaned.get("unit_price") is None:
                self.add_error("unit_price", "Enter a unit price for this item.")
        elif cleaned.get("quantity") is not None or cleaned.get("unit_price") is not None or (cleaned.get("unit") or "").strip():
            self.add_error("description", "Enter an item description.")
        return cleaned


CompanyDocumentLineFormSet = formset_factory(
    CompanyDocumentLineForm,
    extra=10,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
