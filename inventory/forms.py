from django import forms
from .models import Supplier, PurchaseOrder, PurchaseItem, Product
from django import forms


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
            "purchase_cost",
        ]

        widgets = {
            "departments": forms.CheckboxSelectMultiple(),
        }

    def clean(self):
        cleaned_data = super().clean()

        product_type = cleaned_data.get("product_type")
        usage_type = cleaned_data.get("usage_type")
        price = cleaned_data.get("price")
        purchase_cost = cleaned_data.get("purchase_cost")
        unit_multiplier = cleaned_data.get("unit_multiplier")

        # 🔥 PRE-CALCULATE COST PRICE (REAL FIX)
        if purchase_cost and unit_multiplier:
            cost_price = purchase_cost / unit_multiplier
            cleaned_data["cost_price"] = cost_price
            self.instance.cost_price = cost_price   # ✅ THIS FIXES EVERYTHING

        # ----------------------------------
        # FOOD RULES
        # ----------------------------------
        if product_type == "FOOD":
            cleaned_data["usage_type"] = "RESALE"
            cleaned_data["purchase_cost"] = 0
            cleaned_data["cost_price"] = 0
            self.instance.cost_price = 0   # ✅ ALSO IMPORTANT
            return cleaned_data

        # ----------------------------------
        # RESALE VALIDATION
        # ----------------------------------
        if usage_type == "RESALE":

            if not price:
                raise forms.ValidationError("Resale products must have a selling price.")

            if not purchase_cost:
                raise forms.ValidationError("Resale products must have a purchase cost.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)

        purchase_cost = self.cleaned_data.get("purchase_cost")
        unit_multiplier = self.cleaned_data.get("unit_multiplier")

        # 🔥 CRITICAL FIX
        if purchase_cost and unit_multiplier:
            instance.cost_price = purchase_cost / unit_multiplier

        if commit:
            instance.save()
            self.save_m2m()

        return instance

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
