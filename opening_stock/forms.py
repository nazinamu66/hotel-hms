from django import forms

from inventory.models import Department, Product
from linen.models import LinenItem
from maintenance.models import MaintenanceLocation
from rooms.models import Room

from .models import OpeningStockBatch, OpeningStockLine


class OpeningStockBatchForm(forms.ModelForm):
    class Meta:
        model = OpeningStockBatch
        fields = ("reference", "opening_date", "notes")
        widgets = {"opening_date": forms.DateInput(attrs={"type": "date"})}


class OpeningStockLineForm(forms.ModelForm):
    class Meta:
        model = OpeningStockLine
        exclude = ("batch", "created_at")
        widgets = {
            "asset_purchase_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(hotel=hotel, is_active=True).exclude(product_type__in=("EQUIPMENT", "SERVICE")).order_by("name")
        self.fields["department"].queryset = Department.objects.filter(hotel=hotel, is_active=True).order_by("name")
        self.fields["linen_item"].queryset = LinenItem.objects.filter(product__hotel=hotel, is_active=True, product__is_active=True).select_related("product").order_by("product__name")
        self.fields["asset_product"].queryset = Product.objects.filter(hotel=hotel, product_type="EQUIPMENT", is_active=True).order_by("name")
        self.fields["room"].queryset = Room.objects.filter(hotel=hotel).order_by("room_number")
        self.fields["maintenance_location"].queryset = MaintenanceLocation.objects.filter(hotel=hotel, is_active=True, status="ACTIVE").order_by("name")
        self.fields["quantity"].help_text = "Department stock: enter the product's BASE UNIT. Linen: enter whole pieces."
        self.fields["unit_cost"].help_text = "Cost per base unit (department stock or linen)."
        self.fields["asset_value"].help_text = "Total opening value for this individual asset."
        for field in self.fields.values():
            field.required = False
