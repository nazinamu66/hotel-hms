from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from billing.models import Payment
from frontdesk.workflows.common import (
    get_active_folio,
)

def validate_payment(
    folio,
    amount,
    method,
    reference,
):

    if amount <= 0:
        raise ValidationError(
            "Invalid payment amount."
        )

    if amount > folio.balance:
        raise ValidationError(
            "Payment exceeds outstanding balance."
        )

    if (
        method in ["POS", "TRANSFER"]
        and not reference
    ):
        raise ValidationError(
            "Reference is required for this payment method."
        )


def create_payment(
    folio,
    amount,
    method,
    reference,
    note,
    user,
):

    return Payment.objects.create(
        folio=folio,
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
def take_payment(
    room,
    user,
    amount,
    method,
    reference="",
    note="",
):

    folio = get_active_folio(room)

    validate_payment(
        folio,
        amount,
        method,
        reference,
    )

    payment = create_payment(
        folio,
        amount,
        method,
        reference,
        note,
        user,
    )

    post_payment_to_accounting(payment)

    return payment