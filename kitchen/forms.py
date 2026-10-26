from django import forms
from inventory.models import Product
from .models import Recipe, RecipeItem
from .models import IngredientRestockRequest, IngredientRestockItem
from django.forms import inlineformset_factory
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import IngredientRestockRequest, IngredientRestockItem


from django.forms import inlineformset_factory, BaseInlineFormSet
from django import forms
from .models import IngredientRestockRequest, IngredientRestockItem


class BaseIngredientRestockItemFormSet(BaseInlineFormSet):
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
        label="Food Item"
    )

    quantity_produced = forms.IntegerField(
        min_value=1,
        label="Portions Produced"
    )
