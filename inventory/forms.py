from .models import Supplier, PurchaseOrder, PurchaseItem, Product,Department
from django import forms



class ProductForm(forms.ModelForm):

    def __init__(
        self,
        *args,
        hotel=None,
        maintenance_mode=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.hotel = hotel
        self.maintenance_mode = maintenance_mode

        if hotel:
            self.fields["departments"].queryset = (
                Department.objects
                .filter(
                    hotel=hotel,
                    is_active=True,
                )
                .order_by("name")
            )
        else:
            self.fields["departments"].queryset = (
                Department.objects.none()
            )

        if maintenance_mode:

            self.fields["usage_type"].initial = "INTERNAL"
            self.fields["usage_type"].disabled = True

            maintenance_department = (
                Department.objects
                .filter(
                    hotel=hotel,
                    code="MNT",
                    department_type="MAINTENANCE",
                    is_active=True,
                )
                .first()
            )

            if maintenance_department:
                self.fields["departments"].initial = [
                    maintenance_department.pk
                ]

            self.fields["departments"].disabled = True

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

        # ----------------------------------------------------
        # Department security
        # ----------------------------------------------------

        departments = cleaned_data.get("departments")

        if self.hotel and departments:

            invalid_departments = departments.exclude(
                hotel=self.hotel,
            )

            if invalid_departments.exists():
                raise forms.ValidationError(
                    "Products can only be assigned to "
                    "departments belonging to the selected hotel."
                )

        product_type = cleaned_data.get("product_type")
        usage_type = cleaned_data.get("usage_type")
        price = cleaned_data.get("price")
        purchase_cost = cleaned_data.get("purchase_cost")
        unit_multiplier = cleaned_data.get("unit_multiplier")

        # ----------------------------------------------------
        # Cost calculation
        # ----------------------------------------------------

        if purchase_cost and unit_multiplier:
            cost_price = purchase_cost / unit_multiplier
            cleaned_data["cost_price"] = cost_price
            self.instance.cost_price = cost_price

        # ----------------------------------------------------
        # FOOD rules
        # ----------------------------------------------------

        if product_type == "FOOD":

            cleaned_data["usage_type"] = "RESALE"
            cleaned_data["purchase_cost"] = 0
            cleaned_data["cost_price"] = 0

            self.instance.cost_price = 0

            return cleaned_data

        # ----------------------------------------------------
        # RESALE validation
        # ----------------------------------------------------

        if usage_type == "RESALE":

            if not price:
                raise forms.ValidationError(
                    "Resale products must have a selling price."
                )

            if not purchase_cost:
                raise forms.ValidationError(
                    "Resale products must have a purchase cost."
                )

        return cleaned_data

    def save(self, commit=True):

        instance = super().save(commit=False)

        purchase_cost = self.cleaned_data.get("purchase_cost")
        unit_multiplier = self.cleaned_data.get("unit_multiplier")

        if purchase_cost and unit_multiplier:
            instance.cost_price = (
                purchase_cost / unit_multiplier
            )

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
