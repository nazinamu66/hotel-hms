
from .models import (
    Supplier,
    PurchaseOrder,
    PurchaseItem,
    Product,
    Department,
)
from django import forms
from django.core.exceptions import ValidationError
from linen.models import LinenItem


class ProductForm(forms.ModelForm):

    register_as_linen = forms.BooleanField(
        required=False,
        label="Register as linen",
        help_text=(
            "Enable linen circulation for reusable items "
            "such as bedsheets, towels, and pillowcases."
        ),
    )

    linen_par_level = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        label="Linen par level",
    )

    linen_minimum_level = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        label="Linen minimum level",
    )

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

        # ----------------------------------------------------
        # Department queryset
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Maintenance mode
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Existing linen registration
        # ----------------------------------------------------

        existing_linen = None

        if self.instance and self.instance.pk:
            existing_linen = (
                LinenItem.objects
                .filter(product=self.instance)
                .first()
            )

        self.fields["register_as_linen"].initial = bool(
            existing_linen
        )

        self.fields["linen_par_level"].initial = (
            existing_linen.par_level
            if existing_linen
            else 0
        )

        self.fields["linen_minimum_level"].initial = (
            existing_linen.minimum_level
            if existing_linen
            else 0
        )

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

        # ----------------------------------------------------
        # RESALE validation
        # ----------------------------------------------------

        elif usage_type == "RESALE":
            if not price:
                raise forms.ValidationError(
                    "Resale products must have a selling price."
                )

            if not purchase_cost:
                raise forms.ValidationError(
                    "Resale products must have a purchase cost."
                )

        # ----------------------------------------------------
        # Linen validation
        # ----------------------------------------------------

        if cleaned_data.get("register_as_linen"):
            # Use the final usage type after applying FOOD rules.
            final_usage_type = cleaned_data.get("usage_type")

            if final_usage_type != "ASSET":
                raise forms.ValidationError(
                    "Only products with usage type ASSET "
                    "can be registered as linen."
                )

            par_level = cleaned_data.get("linen_par_level")
            minimum_level = cleaned_data.get(
                "linen_minimum_level"
            )

            if par_level is None or minimum_level is None:
                raise forms.ValidationError(
                    "Enter both the linen par level "
                    "and minimum level."
                )

            if minimum_level > par_level:
                raise forms.ValidationError(
                    "Linen minimum level cannot exceed "
                    "the par level."
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
        fields = [
            "name",
            "phone",
            "email",
            "address",
        ]


class PurchaseOrderForm(forms.ModelForm):

    class Meta:
        model = PurchaseOrder
        fields = [
            "supplier",
            "department",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["supplier"].queryset = (
            Supplier.objects
            .filter(is_active=True)
            .order_by("name")
        )


class PurchaseItemForm(forms.ModelForm):

    class Meta:
        model = PurchaseItem
        fields = [
            "product",
            "purchase_quantity",
            "unit_cost",
        ]
