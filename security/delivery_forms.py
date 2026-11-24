from django import forms
from django.contrib.auth import get_user_model
from inventory.models import Department, PurchaseOrder, Supplier
from .delivery_models import SecurityDeliveryReceiver, SecurityDeliveryVisit

User = get_user_model()


class SecurityDeliveryVisitForm(forms.ModelForm):
    class Meta:
        model = SecurityDeliveryVisit
        fields = [
            "supplier", "department", "purchase_order", "delivery_reference",
            "vehicle_registration", "vehicle_description", "driver_name", "delivery_note",
        ]
        widgets = {
            "delivery_note": forms.Textarea(attrs={"rows": 3}),
            "vehicle_registration": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Vehicle registration"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hotel = hotel
        self.fields["supplier"].queryset = Supplier.objects.filter(hotel=hotel, is_active=True).order_by("name") if hotel else Supplier.objects.none()
        self.fields["department"].queryset = Department.objects.filter(hotel=hotel, is_active=True).order_by("name") if hotel else Department.objects.none()
        self.fields["purchase_order"].queryset = PurchaseOrder.objects.filter(
            department__hotel=hotel,
            status__in=("APPROVED", "PARTIALLY_RECEIVED", "RECEIVED"),
        ).select_related("supplier", "department").order_by("-created_at") if hotel else PurchaseOrder.objects.none()
        self.fields["purchase_order"].required = False
        self.fields["vehicle_description"].required = False
        self.fields["driver_name"].required = False
        self.fields["delivery_note"].required = False

    def clean(self):
        cleaned = super().clean()
        po = cleaned.get("purchase_order")
        supplier = cleaned.get("supplier")
        department = cleaned.get("department")
        if po and supplier and department:
            if po.supplier_id != supplier.pk:
                self.add_error("purchase_order", "The selected Purchase Order belongs to a different supplier.")
            if po.department_id != department.pk:
                self.add_error("purchase_order", "The selected Purchase Order is for a different receiving department.")
        return cleaned


class SecurityDeliveryReceiverForm(forms.ModelForm):
    class Meta:
        model = SecurityDeliveryReceiver
        fields = ["department", "user", "note"]
        widgets = {"note": forms.TextInput(attrs={"placeholder": "Optional authorisation note"})}

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hotel = hotel
        self.fields["department"].queryset = Department.objects.filter(hotel=hotel, is_active=True).order_by("name") if hotel else Department.objects.none()
        self.fields["user"].queryset = User.objects.filter(is_active=True, department__hotel=hotel).order_by("first_name", "last_name", "username") if hotel else User.objects.none()
        self.fields["note"].required = False

    def clean(self):
        cleaned = super().clean()
        department, user = cleaned.get("department"), cleaned.get("user")
        if department and user and user.department_id != department.pk:
            self.add_error("user", "The staff member must be assigned to the selected department.")
        return cleaned
