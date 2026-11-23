from decimal import Decimal
from django import forms
from inventory.models import Product
from .models import Recipe, RecipeItem
from billing.models import EventOrderLine
from .models import IngredientRestockRequest, IngredientRestockItem
from django.forms import inlineformset_factory
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import IngredientRestockRequest, IngredientRestockItem


from django.forms import inlineformset_factory, BaseInlineFormSet
from django import forms
from .models import IngredientRestockRequest, IngredientRestockItem


class BaseIngredientRestockItemFormSet(BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        ingredient_field = form.fields.get("ingredient")
        if ingredient_field:
            ingredient_field.label_from_instance = (
                lambda product: f"{product.name} — {product.base_unit}"
            )

    def clean(self):
        super().clean()

        sources = set()
        requester = getattr(self.instance, "requested_by", None)
        requester_department = (
            requester.department
            if requester and getattr(requester, "department_id", None)
            else None
        )
        requester_hotel_id = getattr(requester_department, "hotel_id", None)

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue

            ingredient = form.cleaned_data.get("ingredient")
            quantity = form.cleaned_data.get("quantity")
            source = form.cleaned_data.get("source")

            if ingredient is None and quantity is None and source is None:
                continue

            if quantity is None or quantity <= 0:
                raise forms.ValidationError(
                    "Requested ingredient quantities must be greater than zero."
                )

            if ingredient and requester_hotel_id is not None:
                if ingredient.hotel_id != requester_hotel_id:
                    raise forms.ValidationError(
                        "Requested ingredients must belong to your hotel's inventory."
                    )

            if source:
                sources.add(source)

        if len(sources) > 1:
            raise forms.ValidationError(
                "You cannot mix STORE and DIRECT items in one request."
            )



IngredientRestockItemFormSet = inlineformset_factory(
    IngredientRestockRequest,
    IngredientRestockItem,
    fields=["ingredient", "quantity", "source"],
    extra=1,
    can_delete=True,
    formset=BaseIngredientRestockItemFormSet
)

class IngredientRestockItemForm(forms.ModelForm):
    class Meta:
        model = IngredientRestockItem
        fields = ["ingredient", "quantity", "source"]



class IngredientRestockRequestForm(forms.ModelForm):
    class Meta:
        model = IngredientRestockRequest
        fields = ["note"]    # ✅ correct



class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ["is_active"]


class RecipeItemForm(forms.ModelForm):
    class Meta:
        model = RecipeItem
        fields = [
            "ingredient",
            "quantity",
            "control_mode",
            "tolerance_percent",
        ]

    def clean(self):
        cleaned = super().clean()
        ingredient = cleaned.get("ingredient")
        quantity = cleaned.get("quantity")

        if quantity is None or quantity <= 0:
            raise forms.ValidationError(
                "Quantity per portion must be greater than 0."
            )

        if ingredient and ingredient.product_type != "RAW":
            raise forms.ValidationError(
                "Only RAW products can be used as ingredients."
            )

        control_mode = cleaned.get("control_mode")
        tolerance_percent = cleaned.get("tolerance_percent")
        if tolerance_percent is not None and tolerance_percent < 0:
            raise forms.ValidationError("Tolerance percentage cannot be negative.")
        if control_mode == "TOLERANCE" and (tolerance_percent is None or tolerance_percent <= 0):
            raise forms.ValidationError("Tolerance mode requires a tolerance percentage greater than zero.")

        return cleaned

class IngredientUsageForm(forms.Form):
    ingredient_id = forms.IntegerField(widget=forms.HiddenInput)
    actual_quantity = forms.DecimalField(
        min_value=0.001,
        decimal_places=3,
        max_digits=10
    )
    variance_reason = forms.CharField(required=False, max_length=1000)
    unit = forms.CharField(required=False, widget=forms.HiddenInput)
    unit = forms.CharField(required=False, widget=forms.HiddenInput)

class PreparedFoodForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "reorder_level","price",]

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data



class ProductionBatchForm(forms.Form):
    recipe = forms.ModelChoiceField(
        queryset=Recipe.objects.filter(is_active=True),
        label="Food / Snack Item"
    )

    quantity_produced = forms.IntegerField(
        min_value=1,
        label="Portions Produced"
    )
    event_order_line = forms.ModelChoiceField(
        queryset=EventOrderLine.objects.none(),
        required=False,
        label="Corporate event food line (optional)",
    )

    def clean(self):
        cleaned = super().clean()
        recipe = cleaned.get("recipe")
        event_line = cleaned.get("event_order_line")
        if event_line and event_line.recipe_id != getattr(recipe, "pk", None):
            self.add_error("event_order_line", "The selected event line must use the selected Kitchen recipe.")
        return cleaned


class StaffFoodProductionForm(forms.Form):
    MODE_CHOICES = (
        ("RECIPE", "Use an approved recipe"),
        ("MANUAL", "Enter ingredients manually"),
        ("NO_MEAL", "No staff meal prepared"),
    )

    production_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Production date",
    )
    meal_name = forms.CharField(max_length=150, required=False, label="Meal / menu")
    mode = forms.ChoiceField(choices=MODE_CHOICES, initial="MANUAL", label="Production method")
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.none(), required=False)
    portions_prepared = forms.IntegerField(min_value=1, required=False, label="Portions prepared")
    portions_served = forms.IntegerField(min_value=0, required=False, label="Portions served")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        recipe = cleaned.get("recipe")
        prepared = cleaned.get("portions_prepared")
        served = cleaned.get("portions_served")
        if mode == "NO_MEAL":
            if not cleaned.get("notes", "").strip():
                self.add_error("notes", "Give a reason why no staff meal was prepared.")
            if recipe or prepared or served:
                self.add_error("mode", "A no-meal record cannot include a recipe or portions.")
        else:
            if not cleaned.get("meal_name", "").strip():
                self.add_error("meal_name", "Enter the staff meal description.")
            if not prepared:
                self.add_error("portions_prepared", "Enter the number of portions prepared.")
            if served is None:
                self.add_error("portions_served", "Enter the number of portions served.")
            elif prepared is not None and served > prepared:
                self.add_error("portions_served", "Portions served cannot exceed portions prepared.")
            if mode == "RECIPE" and not recipe:
                self.add_error("recipe", "Select an approved recipe.")
            if mode == "MANUAL" and recipe:
                self.add_error("recipe", "Choose manual entry when not using a recipe.")
        return cleaned


class StaffFoodIngredientForm(forms.Form):
    ingredient = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        label="Ingredient",
    )
    actual_quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        max_digits=12,
        decimal_places=3,
        label="Actual quantity used",
    )
    variance_reason = forms.CharField(required=False, max_length=1000)
