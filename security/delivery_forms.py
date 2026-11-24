from django import forms
from inventory.models import PurchaseItem, PurchaseOrder, Supplier
from .delivery_models import SecurityDeliveryVisit


class SecurityDeliveryVisitForm(forms.ModelForm):
    class Meta:
        model = SecurityDeliveryVisit
        fields = [
            "supplier", "purchase_order", "delivery_reference",
            "vehicle_registration", "vehicle_description", "driver_name", "delivery_note",
        ]
        widgets = {
            "delivery_reference": forms.TextInput(attrs={"class": "form-control", "placeholder": "Supplier delivery note / reference"}),
            "vehicle_registration": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off", "placeholder": "e.g. KAN 123 XY"}),
            "vehicle_description": forms.TextInput(attrs={"class": "form-control", "placeholder": "Truck, van, trailer…"}),
            "driver_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Driver's full name"}),
            "delivery_note": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional arrival note"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        eligible_items = PurchaseItem.objects.filter(
            purchase_order__department__hotel=hotel,
            purchase_order__status__in=("APPROVED", "PARTIALLY_RECEIVED"),
        ).select_related("purchase_order", "purchase_order__supplier", "purchase_order__department").prefetch_related("receipt_lines") if hotel else PurchaseItem.objects.none()
        eligible_po_ids, supplier_ids = set(), set()
        for item in eligible_items:
            if item.outstanding_quantity > 0 and item.purchase_order.supplier_id:
                eligible_po_ids.add(item.purchase_order_id)
                supplier_ids.add(item.purchase_order.supplier_id)
        selected_supplier = self.data.get("supplier") if self.is_bound else self.initial.get("supplier")
        self.fields["supplier"].queryset = Supplier.objects.filter(
            hotel=hotel, is_active=True, pk__in=supplier_ids
        ).order_by("name") if hotel else Supplier.objects.none()
        self.fields["supplier"].widget.attrs.update({"class": "form-select", "data-po-filter": "supplier"})
        self.fields["purchase_order"].queryset = PurchaseOrder.objects.filter(
            pk__in=eligible_po_ids, supplier_id=selected_supplier
        ).select_related("supplier", "department").order_by("-created_at") if selected_supplier else PurchaseOrder.objects.none()
        self.fields["purchase_order"].required = True
        self.fields["purchase_order"].label = "Expected Purchase Order"
        self.fields["purchase_order"].help_text = "Only approved orders with outstanding quantities are listed."
        self.fields["purchase_order"].widget.attrs.update({"class": "form-select", "data-po-filter": "purchase-order"})
        self.fields["purchase_order"].label_from_instance = lambda po: f"PO #{po.pk} · {po.supplier.name} · {po.department.name}"
        for name in ("delivery_reference", "vehicle_registration", "vehicle_description", "driver_name"):
            self.fields[name].widget.attrs.setdefault("class", "form-control")
        self.fields["delivery_note"].required = False
        self.fields["vehicle_description"].required = False
        self.fields["driver_name"].required = False

    def clean(self):
        cleaned = super().clean()
        po, supplier = cleaned.get("purchase_order"), cleaned.get("supplier")
        if not po:
            self.add_error("purchase_order", "Select an expected Purchase Order.")
        elif supplier and po.supplier_id != supplier.pk:
            self.add_error("purchase_order", "The selected Purchase Order belongs to another supplier.")
        elif not any(item.outstanding_quantity > 0 for item in po.items.prefetch_related("receipt_lines")):
            self.add_error("purchase_order", "This Purchase Order has no outstanding quantities.")
        else:
            self.instance.department = po.department
        return cleaned


class SecurityDeliveryGateCheckForm(forms.Form):
    gate_check_status = forms.ChoiceField(
        label="Inspection result",
        choices=SecurityDeliveryVisit.GATE_CHECK_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    gate_check_note = forms.CharField(
        label="Inspection note",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Describe missing quantities, damaged packages or other discrepancies"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("gate_check_status") == SecurityDeliveryVisit.CHECK_PARTIAL and not cleaned.get("gate_check_note", "").strip():
            self.add_error("gate_check_note", "Describe what is missing or discrepant before acknowledging a partial delivery.")
        return cleaned
