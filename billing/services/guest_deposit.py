from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError

from billing.models import (
    Guest,
    GuestDeposit,
    GuestDepositTransaction,
    ReservationDeposit,

)
from accounting.models import BankAccount
from accounting.services.journal import (
    get_system_account,
    record_transaction,
    
)
from billing.services.reservation_deposit import (
    allocate_pending_reservation_guarantees,
)


PAYMENT_METHODS = (
    "CASH",
    "POS",
    "TRANSFER",
)


def validate_guest(guest, hotel):
    if not guest:
        raise ValidationError("Guest is required.")

    if guest.hotel_id != hotel.id:
        raise ValidationError(
            "Guest does not belong to this hotel."
        )


def validate_amount(amount):
    if amount is None or amount <= 0:
        raise ValidationError(
            "Deposit amount must be greater than zero."
        )


def validate_method(method):
    method = (method or "").strip().upper()

    if method not in PAYMENT_METHODS:
        raise ValidationError(
            "Invalid deposit payment method."
        )

    return method


def validate_reference(method, reference):
    if method in ("POS", "TRANSFER") and not reference:
        raise ValidationError(
            "Reference is required for this payment method."
        )


def get_or_create_guest_deposit(guest, hotel):
    deposit, created = (
        GuestDeposit.objects
        .select_for_update()
        .get_or_create(
            guest=guest,
            hotel=hotel,
            defaults={
                "balance": Decimal("0.00"),
            },
        )
    )

    return deposit


@transaction.atomic
def receive_guest_deposit(
    guest,
    hotel,
    amount,
    method,
    user,
    reference="",
    note="",
    bank_account=None,
):
    """
    Receive money from a guest as a deposit.

    Accounting:

        CASH:
            Dr Cash
            Cr Guest Deposits

        POS / TRANSFER:
            Dr Selected Bank Account
            Cr Guest Deposits
    """

    validate_guest(
        guest=guest,
        hotel=hotel,
    )

    validate_amount(amount)

    method = validate_method(method)

    reference = (reference or "").strip()
    note = (note or "").strip()

    validate_reference(
        method=method,
        reference=reference,
    )

    if method in ("POS", "TRANSFER"):

        if not bank_account:
            raise ValidationError(
                "Bank account is required for this payment method."
            )

        if bank_account.hotel_id != hotel.id:
            raise ValidationError(
                "Bank account does not belong to this hotel."
            )

        if not bank_account.is_active:
            raise ValidationError(
                "Selected bank account is inactive."
            )

    cash_account = get_system_account(
        hotel,
        "cash",
    )

    guest_deposits_account = get_system_account(
        hotel,
        "guest_deposits",
    )

    if method == "CASH":
        debit_account = cash_account

    else:
        debit_account = bank_account.account

    deposit = get_or_create_guest_deposit(
        guest=guest,
        hotel=hotel,
    )

    description = (
        f"Guest deposit – {guest.full_name}"
    )

    if method == "CASH":
        description += " (Cash)"
    else:
        description += (
            f" ({bank_account.bank_name})"
        )

    if note:
        description += f" — {note}"

    record_transaction(
        debit_account=debit_account,
        credit_account=guest_deposits_account,
        amount=amount,
        description=description,
        hotel=hotel,
        created_by=user,
        reference=reference or None,
    )

    deposit.balance += amount
    deposit.save(
        update_fields=[
            "balance",
            "updated_at",
        ]
    )

    transaction_record = (
        GuestDepositTransaction.objects.create(
            deposit=deposit,
            transaction_type="RECEIVED",
            amount=amount,
            reference=reference,
            note=note,
            created_by=user,
        )
    )

    allocate_pending_reservation_guarantees(
        guest=guest,
        hotel=hotel,
        user=user,
    )

    return deposit, transaction_record


@transaction.atomic
def apply_guest_deposit(
    folio,
    amount,
    user,
    reference="",
    note="",
):
    """
    Apply an existing guest deposit to a folio.

    Accounting:
        Dr Guest Deposits
        Cr Accounts Receivable

    This does NOT move cash or bank funds because
    the money was already received when the deposit
    was created.
    """

    if not folio:
        raise ValidationError("Folio is required.")

    if folio.is_closed:
        raise ValidationError("Cannot apply deposit to a closed folio.")

    if amount is None or amount <= 0:
        raise ValidationError(
            "Deposit application amount must be greater than zero."
        )

    if amount > folio.balance:
        raise ValidationError(
            "Deposit application exceeds the outstanding folio balance."
        )

    hotel = folio.hotel
    guest = folio.guest

    if not guest:
        raise ValidationError("Folio has no guest.")

    if guest.hotel_id != hotel.id:
        raise ValidationError(
            "Guest does not belong to this hotel."
        )

    deposit = (
        GuestDeposit.objects
        .select_for_update()
        .filter(
            guest=guest,
            hotel=hotel,
        )
        .first()
    )

    if not deposit:
        raise ValidationError(
            "This guest has no deposit balance."
        )

    active_allocations = (
        ReservationDeposit.objects
        .filter(
            guest_deposit=deposit,
            status="GUARANTEED",
        )
        .aggregate(
            total=Sum("allocated_amount")
        )["total"]
        or Decimal("0.00")
    )

    available_balance = deposit.balance - active_allocations

    if available_balance < 0:
        available_balance = Decimal("0.00")

    if amount > available_balance:
        raise ValidationError(
            f"Deposit application exceeds the guest's available "
            f"unallocated deposit balance of ₦{available_balance:,.2f}."
        )

    reference = (reference or "").strip()
    note = (note or "").strip()

    guest_deposits_account = get_system_account(
        hotel,
        "guest_deposits",
    )

    receivable_account = get_system_account(
        hotel,
        "accounts_receivable",
    )

    description = (
        f"Guest deposit applied – {guest.full_name}"
    )

    if note:
        description += f" — {note}"

    record_transaction(
        debit_account=guest_deposits_account,
        credit_account=receivable_account,
        amount=amount,
        description=description,
        hotel=hotel,
        created_by=user,
        reference=reference or None,
    )

    deposit.balance -= amount
    deposit.save(
        update_fields=[
            "balance",
            "updated_at",
        ]
    )

    transaction_record = (
        GuestDepositTransaction.objects.create(
            deposit=deposit,
            transaction_type="APPLIED",
            amount=amount,
            folio=folio,
            reference=reference,
            note=note,
            created_by=user,
        )
    )

    from billing.models import Payment

    payment = Payment.objects.create(
        folio=folio,
        amount=amount,
        method="DEPOSIT",
        reference=reference,
        note=note,
        collected_by=user,
    )

    return payment, transaction_record