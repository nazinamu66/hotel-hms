from django import forms

from accounting.models import Account, BankAccount


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            "code", "name", "description", "account_type", "parent",
            "opening_balance", "allow_posting", "allow_manual_entries", "is_active",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "finance-input", "placeholder": "e.g. 4100"}),
            "name": forms.TextInput(attrs={"class": "finance-input", "placeholder": "e.g. Room Revenue"}),
            "description": forms.Textarea(attrs={"class": "finance-input", "rows": 3, "placeholder": "What is this account used for?"}),
            "account_type": forms.Select(attrs={"class": "finance-input", "id": "id_account_type"}),
            "parent": forms.Select(attrs={"class": "finance-input"}),
            "opening_balance": forms.NumberInput(attrs={"class": "finance-input", "step": "0.01", "min": "0", "placeholder": "0.00"}),
            "allow_posting": forms.CheckboxInput(attrs={"class": "finance-check"}),
            "allow_manual_entries": forms.CheckboxInput(attrs={"class": "finance-check"}),
            "is_active": forms.CheckboxInput(attrs={"class": "finance-check"}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        if hotel:
            self.fields["parent"].queryset = (
                Account.objects.filter(hotel=hotel, is_active=True).order_by("code")
            )
        self.fields["parent"].required = False
        self.fields["description"].required = False
        self.fields["opening_balance"].required = False


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ["bank_name", "account_name", "account_number", "currency", "is_default", "is_active"]
        widgets = {
            "bank_name": forms.TextInput(attrs={"class": "finance-input", "placeholder": "e.g. Access Bank"}),
            "account_name": forms.TextInput(attrs={"class": "finance-input", "placeholder": "Name registered with the bank"}),
            "account_number": forms.TextInput(attrs={"class": "finance-input", "placeholder": "Bank account number"}),
            "currency": forms.TextInput(attrs={"class": "finance-input", "placeholder": "NGN"}),
            "is_default": forms.CheckboxInput(attrs={"class": "finance-check"}),
            "is_active": forms.CheckboxInput(attrs={"class": "finance-check"}),
        }
