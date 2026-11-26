from django import forms
from django.utils import timezone

from accounting.models import BankAccount
from .models import GymMember, GymMembershipPlan


class GymMemberForm(forms.ModelForm):
    class Meta:
        model = GymMember
        fields = (
            "first_name", "last_name", "phone", "email", "gender",
            "date_of_birth", "address", "photograph",
            "emergency_contact_name", "emergency_contact_phone", "notes",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class GymMembershipPlanForm(forms.ModelForm):
    class Meta:
        model = GymMembershipPlan
        fields = ("package", "price", "is_active")
        widgets = {"price": forms.NumberInput(attrs={"min": "0", "step": "0.01"})}


class GymMembershipCheckoutForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=GymMembershipPlan.objects.none())
    start_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    payment_method = forms.ChoiceField(choices=(
        ("CASH", "Cash"),
        ("POS", "POS"),
        ("TRANSFER", "Bank Transfer"),
    ))
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(),
        required=False,
        empty_label="Select receiving bank account",
    )
    payment_reference = forms.CharField(
        max_length=100, required=False,
        help_text="POS slip number or bank transfer reference, if available.",
    )

    def __init__(self, *args, hotel, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = GymMembershipPlan.objects.filter(
            hotel=hotel, is_active=True, price__gt=0
        )
        self.fields["plan"].label_from_instance = (
            lambda plan: f"{plan.get_package_display()} · ₦{plan.price:,.2f}"
        )
        self.fields["bank_account"].queryset = BankAccount.objects.filter(
            hotel=hotel, is_active=True, account__is_active=True
        ).select_related("account").order_by("-is_default", "bank_name")
        self.fields["bank_account"].label_from_instance = (
            lambda item: f"{item.bank_name} · {item.account_name} · {item.account_number}"
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_method") in {"POS", "TRANSFER"} and not cleaned.get("bank_account"):
            self.add_error("bank_account", "Select the bank account receiving this payment.")
        return cleaned
