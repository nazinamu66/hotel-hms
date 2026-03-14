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

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue

            sources.add(form.cleaned_data.get("source"))

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
        mode = cleaned.get("control_mode")
        tolerance = cleaned.get("tolerance_percent")

        if ingredient and ingredient.product_type != "RAW":
            raise forms.ValidationError(
                "Only RAW products can be used as ingredients."
            )

        if mode == "TOLERANCE":
            if tolerance is None or tolerance <= 0:
                raise forms.ValidationError(
                    "Tolerance percent must be greater than 0."
                )
        else:
            cleaned["tolerance_percent"] = 0

        return cleaned

class IngredientUsageForm(forms.Form):
    ingredient_id = forms.IntegerField(widget=forms.HiddenInput)
    actual_quantity = forms.DecimalField(
        min_value=0.001,
        decimal_places=3,
        max_digits=10
    )

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
