from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from maintenance.models import MaintenanceAsset
from inventory.models import Department
from .models import PropertyExitPass

User = get_user_model()


class PropertyExitPassForm(forms.ModelForm):
    class Meta:
        model = PropertyExitPass
        fields = [
            "pass_type", "staff_name", "staff_department", "staff_user",
            "item_is_equipment", "equipment_asset", "item_description",
            "serial_number", "quantity", "purpose", "expected_return_date",
            "approval_reference", "authorisation_note", "staff_acknowledgement",
        ]
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "expected_return_date": forms.DateInput(attrs={"type": "date"}),
            "authorisation_note": forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}),
        }
        help_texts = {
            "item_is_equipment": "Tick for equipment. Serial number is optional; describe the item if it has no serial number.",
            "authorisation_note": "Optional scan or photo of the handwritten management note.",
            "staff_acknowledgement": "Confirm the staff member has acknowledged this authorisation.",
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hotel = hotel
        self.fields["equipment_asset"].queryset = MaintenanceAsset.objects.filter(hotel=hotel, is_active=True).order_by("name") if hotel else MaintenanceAsset.objects.none()
        self.fields["staff_user"].queryset = User.objects.filter(hotel=hotel, is_active=True).order_by("username") if hotel else User.objects.none()
        self.fields["equipment_asset"].required = False
        self.fields["serial_number"].required = False
        self.fields["staff_user"].required = False
        self.fields["staff_department"].required = False
        self.fields["approval_reference"].required = False
        self.fields["authorisation_note"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pass_type") == PropertyExitPass.TEMPORARY and not cleaned.get("expected_return_date"):
            self.add_error("expected_return_date", "Set the expected return date for a temporary removal.")
        if cleaned.get("pass_type") == PropertyExitPass.PERMANENT and cleaned.get("expected_return_date"):
            self.add_error("expected_return_date", "A permanent release does not require a return date.")
        if not cleaned.get("item_is_equipment") and cleaned.get("equipment_asset"):
            self.add_error("equipment_asset", "Mark the item as equipment before selecting a registered asset.")
        if not cleaned.get("approval_reference") and not cleaned.get("authorisation_note"):
            self.add_error("approval_reference", "Enter the handwritten note reference or attach a scan/photo of the management authorisation.")
        return cleaned
