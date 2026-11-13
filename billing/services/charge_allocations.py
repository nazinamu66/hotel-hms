from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from billing.models import Charge, ChargePaymentAllocation, Folio, Payment


ZERO = Decimal("0.00")


def _allocated_to_charge(charge):
    return (
        ChargePaymentAllocation.objects.filter(charge=charge)
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )


def _allocated_from_payment(payment):
    return (
        ChargePaymentAllocation.objects.filter(payment=payment)
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )


def charge_outstanding(charge):
    """Outstanding amount covered by the new allocation ledger."""
    if charge.amount <= 0:
        return ZERO
    return max(charge.amount - _allocated_to_charge(charge), ZERO)


def payment_unallocated(payment):
    """Unallocated portion of a positive receipt; negative payments are excluded."""
    if payment.amount <= 0:
        return ZERO
    return max(payment.amount - _allocated_from_payment(payment), ZERO)


@transaction.atomic
def allocate_payment_fifo(payment):
    """
    Allocate a newly received general folio payment to the oldest positive
    outstanding charges. Historical payments are deliberately not backfilled.
    """
    payment = Payment.objects.select_for_update().select_related("folio").get(pk=payment.pk)
    if payment.amount <= 0:
        raise ValidationError("Only positive payments can be allocated.")
    if payment.method == "POS" and payment.reference.startswith("POS-"):
        raise ValidationError("POS receipts must be allocated to their matching order charge.")

    folio = Folio.objects.select_for_update().get(pk=payment.folio_id)
    remaining = payment_unallocated(payment)
    if remaining <= 0:
        return []

    charges = (
        Charge.objects.select_for_update()
        .filter(folio=folio, amount__gt=0)
        .order_by("created_at", "id")
    )
    created = []
    for charge in charges:
        if remaining <= 0:
            break
        outstanding = charge_outstanding(charge)
        if outstanding <= 0:
            continue
        amount = min(remaining, outstanding)
        created.append(ChargePaymentAllocation.objects.create(
            charge=charge,
            payment=payment,
            amount=amount,
            allocation_type="FIFO",
            created_by=payment.collected_by,
        ))
        remaining -= amount
    return created


@transaction.atomic
def allocate_payment_to_charge(payment, charge):
    """Allocate a POS receipt only to the charge created for that POS order."""
    payment = Payment.objects.select_for_update().select_related("folio").get(pk=payment.pk)
    charge = Charge.objects.select_for_update().select_related("folio").get(pk=charge.pk)

    if payment.amount <= 0:
        raise ValidationError("Only positive payments can be allocated.")
    if payment.folio_id != charge.folio_id:
        raise ValidationError("Payment and charge must belong to the same folio.")
    if payment.folio.hotel_id != charge.folio.hotel_id:
        raise ValidationError("Payment and charge must belong to the same hotel.")
    if charge.amount <= 0:
        raise ValidationError("Only positive charges can receive allocations.")

    remaining_payment = payment_unallocated(payment)
    remaining_charge = charge_outstanding(charge)
    if remaining_payment <= 0:
        raise ValidationError("This payment has no unallocated balance.")
    if remaining_charge <= 0:
        raise ValidationError("This charge is already fully allocated.")
    if remaining_payment > remaining_charge:
        raise ValidationError("POS payment exceeds the outstanding amount of its matching charge.")

    return ChargePaymentAllocation.objects.create(
        charge=charge,
        payment=payment,
        amount=remaining_payment,
        allocation_type="POS_DIRECT",
        created_by=payment.collected_by,
    )


def get_charge_allocation_status(charge):
    allocated = _allocated_to_charge(charge)
    return {
        "allocated": allocated,
        "outstanding": max(charge.amount - allocated, ZERO),
    }
