from django.db import transaction
from django.core.exceptions import ValidationError

from billing.models import Payment
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

    if amount > folio.balance:
        raise ValidationError(
            "Payment exceeds outstanding balance."
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