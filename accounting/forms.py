from django import forms

from accounting.models import Account, BankAccount


class AccountForm(forms.ModelForm):

    class Meta:
        model = Account

        fields = [
            "code",
            "name",
            "description",
            "account_type",
            "parent",
            "opening_balance",
            "allow_posting",
            "allow_manual_entries",
            "is_active",
        ]

        widgets = {
            "description": forms.Textarea(
                attrs={"rows": 3}
            ),
        }

    def __init__(self, *args, hotel=None, **kwargs):

        super().__init__(*args, **kwargs)

        if hotel:
            self.fields["parent"].queryset = (
                Account.objects
                .filter(hotel=hotel)
                .order_by("code")
            )


class BankAccountForm(forms.ModelForm):

    class Meta:
        model = BankAccount

        fields = [
            "bank_name",
            "account_name",
            "account_number",
            "currency",
            "is_default",
            "is_active",
        ]

        widgets = {
            "currency": forms.TextInput(
                attrs={
                    "placeholder": "NGN"
                }
            ),
        }