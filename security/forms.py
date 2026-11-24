from django import forms
from django.contrib.auth import get_user_model
from maintenance.models import MaintenanceAsset
from .models import PropertyExitPass, VehicleVisit, VisitorVisit

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
        for name in ("equipment_asset", "serial_number", "staff_user", "staff_department", "approval_reference", "authorisation_note"):
            self.fields[name].required = False

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


class VehicleVisitForm(forms.ModelForm):
    class Meta:
        model = VehicleVisit
        fields = ["make", "other_make", "model", "plate_number", "entry_note"]
        widgets = {
            "model": forms.TextInput(attrs={"placeholder": "Optional"}),
            "plate_number": forms.TextInput(attrs={"placeholder": "e.g. ABC-123XY", "autocomplete": "off"}),
            "entry_note": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional visitor or vehicle details"}),
        }
        help_texts = {
            "make": "Choose the vehicle manufacturer. Select Other if it is not listed.",
            "plate_number": "Enter the registration exactly as displayed on the vehicle plate.",
        }

    def clean_plate_number(self):
        return " ".join(self.cleaned_data["plate_number"].upper().split())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("make") == "OTHER" and not cleaned.get("other_make", "").strip():
            self.add_error("other_make", "Enter the vehicle make.")
        if cleaned.get("make") != "OTHER":
            cleaned["other_make"] = ""
        plate = cleaned.get("plate_number")
        if plate and self.hotel:
            active = VehicleVisit.objects.filter(hotel=self.hotel, plate_number=plate, exited_at__isnull=True)
            if self.instance.pk:
                active = active.exclude(pk=self.instance.pk)
            if active.exists():
                self.add_error("plate_number", "This vehicle is already recorded as inside the hotel.")
        return cleaned

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hotel = hotel
        self.fields["other_make"].required = False
        self.fields["model"].required = False
        self.fields["entry_note"].required = False


class VisitorVisitForm(forms.ModelForm):
    class Meta:
        model = VisitorVisit
        fields = ['visitor_type', 'visitor_name', 'phone', 'id_type', 'id_number', 'purpose', 'vehicle_plate', 'guest_name', 'guest_room_number', 'host_staff', 'host_staff_name', 'host_department', 'maintenance_ticket', 'supplier', 'work_description', 'entry_note']
        widgets = {'purpose': forms.Textarea(attrs={'rows': 3}), 'entry_note': forms.Textarea(attrs={'rows': 2}), 'vehicle_plate': forms.TextInput(attrs={'placeholder': 'Optional registration'})}

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        from maintenance.models import MaintenanceTicket
        from inventory.models import Supplier
        self.hotel = hotel
        if hotel:
            self.instance.hotel = hotel
        self.fields['host_staff'].queryset = User.objects.filter(is_active=True).filter(Q(hotel=hotel) | Q(department__hotel=hotel)).distinct().order_by('first_name', 'last_name', 'username') if hotel else User.objects.none()
        self.fields['maintenance_ticket'].queryset = MaintenanceTicket.objects.filter(Q(room__hotel=hotel) | Q(location__hotel=hotel)).select_related('room', 'location').order_by('-created_at') if hotel else MaintenanceTicket.objects.none()
        self.fields['supplier'].queryset = Supplier.objects.filter(hotel=hotel).order_by('name') if hotel else Supplier.objects.none()
        for name in ['phone', 'id_type', 'id_number', 'vehicle_plate', 'guest_name', 'guest_room_number', 'host_staff', 'host_staff_name', 'host_department', 'maintenance_ticket', 'supplier', 'work_description', 'entry_note']:
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get('visitor_type')
        if kind == VisitorVisit.GUEST and not cleaned.get('guest_name', '').strip():
            self.add_error('guest_name', 'Enter the guest being visited.')
        if kind == VisitorVisit.STAFF and not cleaned.get('host_staff') and not cleaned.get('host_staff_name', '').strip():
            self.add_error('host_staff_name', 'Select or enter the staff member being visited.')
        if kind == VisitorVisit.OUTSOURCED and not cleaned.get('maintenance_ticket') and not cleaned.get('work_description', '').strip():
            self.add_error('work_description', 'Describe the outsourced work or select a Maintenance work order.')
        return cleaned


class StaffExitPassForm(forms.ModelForm):
    class Meta:
        from .models import StaffExitPass
        model = StaffExitPass
        fields = ["employee", "pass_type", "destination", "purpose", "expected_return_at"]
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 3}),
            "expected_return_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        self.hotel = hotel
        self.fields["employee"].queryset = User.objects.filter(is_active=True).exclude(
            role__in=["ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT"]
        ).filter(Q(hotel=hotel) | Q(department__hotel=hotel)).distinct().order_by("first_name", "last_name", "username") if hotel else User.objects.none()

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        if employee and self.hotel:
            employee_hotel = employee.hotel_id or (employee.department.hotel_id if employee.department_id else None)
            if employee_hotel != self.hotel.pk:
                self.add_error("employee", "Select an employee assigned to this hotel.")
        return cleaned
