from django.db import transaction
from django.core.exceptions import ValidationError

from billing.models import Payment, Folio, GuestDeposit, GuestDepositTransaction
from frontdesk.workflows.common import get_active_folio


def validate_payment(
    folio,
    amount,
    method,
    reference,
    bank_account=None,
):
    if amount <= 0:
        raise ValidationError(
            "Invalid payment amount."
        )


    method = (method or "").strip().upper()

    if method not in ["CASH", "POS", "TRANSFER", "DEPOSIT"]:
        raise ValidationError(
            "Invalid payment method."
        )

    if (
        method in ["POS", "TRANSFER"]
        and not reference
    ):
        raise ValidationError(
            "Reference is required for this payment method."
        )

    if (
        method in ["POS", "TRANSFER"]
        and not bank_account
    ):
        raise ValidationError(
            "Bank account is required for this payment method."
        )

    if method == "DEPOSIT" and bank_account:
        raise ValidationError(
            "Bank account is not used for guest deposit payments."
        )


def create_payment(
    folio,
    amount,
    method,
    reference,
    note,
    user,
    bank_account=None,
):
    account = None

    if bank_account is not None:
        account = bank_account.account

    return Payment.objects.create(
        folio=folio,
        allocation_tracking_enabled=True,
        account=account,
        amount=amount,
        method=method,
        reference=reference,
        note=note,
        collected_by=user,
    )


def post_payment_to_accounting(payment):
    from accounting.services.postings.payment import (
        post_payment,
    )

    post_payment(payment)


@transaction.atomic
def take_payment_for_folio(
    folio,
    user,
    amount,
    method,
    reference="",
    note="",
    bank_account=None,
):
    method = (method or "").strip().upper()
    folio = Folio.objects.select_for_update().select_related("guest", "hotel").get(pk=folio.pk)

    validate_payment(
        folio=folio,
        amount=amount,
        method=method,
        reference=reference,
        bank_account=bank_account,
    )

    payment = create_payment(
        folio=folio,
        amount=amount,
        method=method,
        reference=reference,
        note=note,
        user=user,
        bank_account=bank_account,
    )

    post_payment_to_accounting(payment)

    from billing.services.charge_allocations import allocate_payment_fifo, payment_unallocated
    allocate_payment_fifo(payment)

    # Any receipt amount not applied to eligible folio charges is retained
    # as a guest credit. Cash/bank was already debited and A/R credited by
    # post_payment; this entry reclassifies only the excess from A/R to the
    # guest-deposit liability, without recording the cash twice.
    unapplied = payment_unallocated(payment)
    if unapplied > 0:
        if not folio.guest_id:
            raise ValidationError(
                "An overpayment requires a guest-linked folio so the excess can be credited safely."
            )
        from accounting.services.journal import record_transaction
        from accounting.services.journal import get_system_account

        deposit, _ = GuestDeposit.objects.select_for_update().get_or_create(
            guest=folio.guest,
            hotel=folio.hotel,
            defaults={"balance": Decimal("0.00")},
        )
        record_transaction(
            debit_system_key="accounts_receivable",
            credit_system_key="guest_deposits",
            amount=unapplied,
            description=f"Unapplied folio payment credit – {folio.guest.full_name}",
            hotel=folio.hotel,
            created_by=user,
            reference=f"FOLIO-OVERPAYMENT-{payment.pk}",
        )
        deposit.balance += unapplied
        deposit.save(update_fields=["balance", "updated_at"])
        GuestDepositTransaction.objects.create(
            deposit=deposit,
            transaction_type="UNAPPLIED_CREDIT",
            amount=unapplied,
            folio=folio,
            reference=payment.reference or f"PAYMENT-{payment.pk}",
            note=f"Unapplied excess from folio payment #{payment.pk}. {payment.note}".strip(),
            created_by=user,
        )

    return payment


@transaction.atomic
def take_payment(
    room,
    user,
    amount,
    method,
    reference="",
    note="",
    bank_account=None,
):
    folio = get_active_folio(room)

    return take_payment_for_folio(
        folio=folio,
        user=user,
        amount=amount,
        method=method,
        reference=reference,
        note=note,
        bank_account=bank_account,
    )