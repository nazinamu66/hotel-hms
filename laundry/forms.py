from django import forms

from .models import LaundryService


class LaundryServiceForm(forms.ModelForm):

    class Meta:
        model = LaundryService
        fields = [
            "name",
            "code",
            "unit_price",
            "is_active",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Shirt, Jumper, Babba Riga",
                }
            ),
            "code": forms.TextInput(
                attrs={
                    "placeholder": "e.g. SHIRT",
                }
            ),
            "unit_price": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.01",
                }
            ),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()