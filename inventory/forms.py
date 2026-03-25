from django import forms
from .models import Supplier, PurchaseOrder, PurchaseItem, Product
from django import forms


class ProductForm(forms.ModelForm):

    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "barcode",
            "product_type",
            "base_unit",
            "purchase_unit",
            "unit_multiplier",
            "supply_source",
            "departments",
            "usage_type",
            "reorder_level",
            "price",
            "cost_price",   # ✅ ADD THIS
        ]

        widgets = {
            "departments": forms.CheckboxSelectMultiple(),
        }

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "email", "address"]

class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "department"]
        
class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = ["product", "purchase_quantity", "unit_cost"]
