from django.db import models
from django.conf import settings
from django.utils.text import slugify
from decimal import Decimal
from django.db.models import Sum
from inventory.models import Hotel
from inventory.models import Supplier
from accounting.constants import DEBIT_NORMAL_TYPES



class Account(models.Model):

    ACCOUNT_TYPES = [

        ("cash", "Cash"),
        ("bank", "Bank"),
        ("current_asset", "Current Asset"),
        ("inventory", "Inventory"),
        ("fixed_asset", "Fixed Asset"),
        ("current_liability", "Current Liability"),
        ("long_term_liability", "Long-Term Liability"),
        ("equity", "Equity"),
        ("income", "Income"),
        ("other_income", "Other Income"),
        ("cogs", "Cost of Goods Sold"),
        ("expense", "Expense"),
        ("other_expense", "Other Expense"),
    ]

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="accounts"
    )

    code = models.CharField(max_length=20)

    name = models.CharField(max_length=255)
    description = models.TextField(
    blank=True,
)

    slug = models.SlugField(blank=True)

    account_type = models.CharField(
        max_length=20,
        choices=ACCOUNT_TYPES
    )

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE
    )

    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00")
    )

    system_key = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )
    allow_manual_entries = models.BooleanField(
        default=True,
    )

    is_system = models.BooleanField(
        default=False,
    )

    allow_posting = models.BooleanField(
        default=True,
    )
    display_order = models.PositiveIntegerField(
        default=0,
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "slug"],
                name="unique_account_slug_per_hotel",
            ),
            models.UniqueConstraint(
                fields=["hotel", "code"],
                name="unique_account_code_per_hotel",
            ),
        ]

    def save(self, *args, **kwargs):
        # System accounts keep stable slugs because
        # accounting services depend on them.
        if self.is_system:
            if not self.slug:
                self.slug = slugify(self.name)
        # User-created accounts get a hotel-safe,
        # code-based slug.
        else:
            if not self.pk:
                self.slug = f"{slugify(self.name)}-{slugify(self.code)}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"
    

    # from django.db.models import Sum
    # from decimal import Decimal

    def get_balance(self):

        debit = self.journalline_set.aggregate(
            total=Sum("debit")
        )["total"] or Decimal("0.00")

        credit = self.journalline_set.aggregate(
            total=Sum("credit")
        )["total"] or Decimal("0.00")

        if self.account_type in DEBIT_NORMAL_TYPES:
            return self.opening_balance + debit - credit

        return self.opening_balance + credit - debit    
    

from django.core.exceptions import ValidationError


class BankAccount(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )

    account = models.OneToOneField(
        Account,
        on_delete=models.PROTECT,
        related_name="bank_account",
    )

    bank_name = models.CharField(
        max_length=100
    )

    account_name = models.CharField(
        max_length=150
    )

    account_number = models.CharField(
        max_length=50
    )

    currency = models.CharField(
        max_length=10,
        default="NGN",
    )

    is_default = models.BooleanField(
        default=False
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def clean(self):

        # Account may not exist yet during ModelForm validation.
        # Only validate it when it has actually been assigned.

        if self.account_id:

            account = self.account

            if account.account_type != "bank":
                raise ValidationError({
                    "account": (
                        "BankAccount must be linked "
                        "to a Bank account."
                    )
                })

            if self.hotel_id and self.hotel_id != account.hotel_id:
                raise ValidationError({
                    "account": (
                        "Bank account and accounting "
                        "account must belong to the same hotel."
                    )
                })

    def __str__(self):
        return f"{self.bank_name} ({self.account_number})"
    

class AccountingPeriod(models.Model):

    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.CASCADE)

    start_date = models.DateField()
    end_date = models.DateField()

    is_closed = models.BooleanField(default=False)

    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.start_date} → {self.end_date} | Closed: {self.is_closed}"


class BusinessDay(models.Model):

    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.CASCADE)

    date = models.DateField()

    is_closed = models.BooleanField(default=False)

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("hotel", "date")

    def __str__(self):
        return f"{self.hotel} - {self.date} ({'Closed' if self.is_closed else 'Open'})"
    

class JournalEntry(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="journal_entries"
    )

    date = models.DateField()

    description = models.CharField(max_length=255)

    reference = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )
    
    entry_type = models.CharField(
        max_length=20,
        choices=[
            ("NORMAL", "Normal"),
            ("SALE", "Sale"),
            ("COGS", "COGS"),
            ("PURCHASE", "Purchase"),
            ("CLOSING", "Closing"),
            ("PRODUCTION", "Production"),
        ],
        default="NORMAL"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    business_day = models.ForeignKey(
        BusinessDay,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
    class Meta:
        unique_together = ("hotel", "reference", "entry_type")

    def __str__(self):
        return f"JE-{self.id} | {self.date}"

class JournalLine(models.Model):

    journal = models.ForeignKey(
        JournalEntry,
        related_name="lines",
        on_delete=models.CASCADE
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT
    )

    debit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00")
    )

    credit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00")
    )

    def __str__(self):
        return f"{self.account.name} | D:{self.debit} C:{self.credit}"

class SupplierLedger(models.Model):

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name="ledger_entries"
    )

    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    entry_type = models.CharField(
        max_length=10,
        choices=(
            ("debit", "Debit"),
            ("credit", "Credit")
        )
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.supplier.name} {self.entry_type} {self.amount}"


class ExpenseEntry(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE
    )

    expense_account = models.ForeignKey(
        Account,
        related_name="expense_entries",
        on_delete=models.PROTECT
    )

    payment_account = models.ForeignKey(
        Account,
        related_name="expense_payments",
        on_delete=models.PROTECT
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    description = models.TextField(blank=True)

    date = models.DateField()

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL
    )

    journal_entry = models.ForeignKey(
        JournalEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.expense_account.name} - {self.amount}"


