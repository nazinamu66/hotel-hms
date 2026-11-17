from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import BankAccount
from accounting.services.journal import get_system_account, record_transaction
from billing.models import Company, CompanyDeposit, CompanyDepositTransaction


PAYMENT_METHODS = ("CASH", "POS", "TRANSFER")


def _amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid deposit amount.")
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Deposit amount must be greater than zero.")
    return amount.quantize(Decimal("0.01"))


def _method_and_bank(hotel, method, bank_account):
    method = (method or "").strip().upper()
    if method not in PAYMENT_METHODS:
        raise ValidationError("Select Cash, POS or Bank Transfer.")
    if method == "CASH":
        if bank_account:
            raise ValidationError("A bank account must not be selected for cash transactions.")
        return method, None
    if not bank_account:
        raise ValidationError("Select the hotel bank account receiving or paying the funds.")
    if bank_account.hotel_id != hotel.id or not bank_account.is_active:
        raise ValidationError("Selected bank account is inactive or belongs to another hotel.")
    return method, bank_account


def _company_deposit(company, hotel):
    if not company:
        raise ValidationError("Corporate client is required.")
    if company.hotel_id != hotel.id:
        raise ValidationError("Corporate client does not belong to this hotel.")
    if not company.is_active:
        raise ValidationError("This corporate client is inactive.")
    deposit, _ = CompanyDeposit.objects.select_for_update().get_or_create(
        hotel=hotel,
        company=company,
        defaults={"balance": Decimal("0.00")},
    )
    return deposit


def _cash_or_bank_account(hotel, method, bank_account):
    if method == "CASH":
        return get_system_account(hotel, "cash")
    return bank_account.account


@transaction.atomic
def receive_company_deposit(
    *, company, hotel, amount, method, user,
    reference="", note="", bank_account=None,
):
    """Receive company funds and credit the Corporate Deposits liability."""
    if not hotel:
        raise ValidationError("Hotel is required.")
    amount = _amount(amount)
    method, bank_account = _method_and_bank(hotel, method, bank_account)
    reference = (reference or "").strip()
    note = (note or "").strip()
    if method in ("POS", "TRANSFER") and not reference:
        raise ValidationError("A transaction reference is required for POS or bank transfer.")

    deposit = _company_deposit(company, hotel)
    record = CompanyDepositTransaction.objects.create(
        deposit=deposit,
        transaction_type="RECEIVED",
        amount=amount,
        payment_method=method,
        bank_account=bank_account,
        reference=reference,
        note=note,
        created_by=user,
    )
    debit_account = _cash_or_bank_account(hotel, method, bank_account)
    liability = get_system_account(hotel, "corporate_deposits")
    description = f"Corporate deposit received – {company.name} ({method})"
    if note:
        description += f" — {note}"
    record_transaction(
        debit_account=debit_account,
        credit_account=liability,
        amount=amount,
        description=description,
        hotel=hotel,
        created_by=user,
        reference=f"CORP-DEP-TXN-{record.pk}",
    )
    deposit.balance += amount
    deposit.save(update_fields=["balance", "updated_at"])
    return deposit, record


@transaction.atomic
def refund_company_deposit(
    *, company, hotel, amount, method, user,
    reference="", note="", bank_account=None,
):
    """Refund unallocated corporate deposit funds and reduce the liability."""
    if not hotel:
        raise ValidationError("Hotel is required.")
    amount = _amount(amount)
    method, bank_account = _method_and_bank(hotel, method, bank_account)
    reference = (reference or "").strip()
    note = (note or "").strip()
    if method in ("POS", "TRANSFER") and not reference:
        raise ValidationError("A refund reference is required for POS or bank transfer.")

    deposit = CompanyDeposit.objects.select_for_update().filter(
        hotel=hotel, company=company,
    ).first()
    if deposit is None:
        raise ValidationError("This company has no deposit balance.")
    if company.hotel_id != hotel.id:
        raise ValidationError("Corporate client does not belong to this hotel.")
    if amount > deposit.balance:
        raise ValidationError(
            f"Refund exceeds the available company deposit balance of ₦{deposit.balance:,.2f}."
        )

    record = CompanyDepositTransaction.objects.create(
        deposit=deposit,
        transaction_type="REFUNDED",
        amount=amount,
        payment_method=method,
        bank_account=bank_account,
        reference=reference,
        note=note,
        created_by=user,
    )
    liability = get_system_account(hotel, "corporate_deposits")
    credit_account = _cash_or_bank_account(hotel, method, bank_account)
    description = f"Corporate deposit refunded – {company.name} ({method})"
    if note:
        description += f" — {note}"
    record_transaction(
        debit_account=liability,
        credit_account=credit_account,
        amount=amount,
        description=description,
        hotel=hotel,
        created_by=user,
        reference=f"CORP-DEP-TXN-{record.pk}",
    )
    deposit.balance -= amount
    deposit.save(update_fields=["balance", "updated_at"])
    return deposit, record
