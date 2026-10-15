from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from accounting.services.journal import (
    get_account,
    post_journal_entry,
    record_transaction
)

from billing.models import RefundRequest


def validate_refund_request_source(refund_request):
    if refund_request.source_type == "POS_ORDER":
        if not refund_request.pos_order_id:
            raise ValidationError(
                "Restaurant refund request must have a POS order."
            )

        if refund_request.charge_id:
            raise ValidationError(
                "Restaurant refund request cannot also reference a charge."
            )

    elif refund_request.source_type == "CHARGE":
        if not refund_request.charge_id:
            raise ValidationError(
                "Charge refund request must have a charge."
            )

        if refund_request.pos_order_id:
            raise ValidationError(
                "Charge refund request cannot also reference a POS order."
            )

    else:
        raise ValidationError("Invalid refund request source.")


def validate_requester(refund_request):
    user = refund_request.requested_by

    if not user:
        raise ValidationError("Refund request must have a requester.")

    if not user.is_active:
        raise ValidationError("Refund requester is inactive.")


def validate_pending(refund_request):
    if refund_request.status != "PENDING":
        raise ValidationError(
            "Only pending refund requests can be processed."
        )


@transaction.atomic
def create_refund_request(
    *,
    source_type,
    requested_by,
    reason,
    pos_order=None,
    charge=None,
):
    if not reason or not reason.strip():
        raise ValidationError("Refund reason is required.")
    
    if source_type == "POS_ORDER" and pos_order:
        existing_request = (
            RefundRequest.objects
            .filter(
                source_type="POS_ORDER",
                pos_order=pos_order,
                status__in=[
                    "PENDING",
                    "APPROVED",
                    "COMPLETED",
                ],
            )
            .first()
        )

        if existing_request:
            raise ValidationError(
                f"A refund request already exists for this order "
                f"(Request #{existing_request.id}, "
                f"status: {existing_request.get_status_display()})."
            )

    if source_type == "CHARGE" and charge:
        existing_request = (
            RefundRequest.objects
            .filter(
                source_type="CHARGE",
                charge=charge,
                status__in=[
                    "PENDING",
                    "APPROVED",
                    "COMPLETED",
                ],
            )
            .first()
        )

        if existing_request:
            raise ValidationError(
                f"A refund request already exists for this charge "
                f"(Request #{existing_request.id}, "
                f"status: {existing_request.get_status_display()})."
            )

    refund_request = RefundRequest.objects.create(
        source_type=source_type,
        pos_order=pos_order,
        charge=charge,
        requested_by=requested_by,
        reason=reason.strip(),
        status="PENDING",
    )

    validate_refund_request_source(refund_request)
    validate_requester(refund_request)

    return refund_request


@transaction.atomic
def approve_refund_request(refund_request, manager):
    validate_pending(refund_request)

    if not manager:
        raise ValidationError("Manager is required.")

    if not manager.is_active:
        raise ValidationError("Approving manager is inactive.")

    refund_request.status = "APPROVED"
    refund_request.approved_by = manager
    refund_request.approved_at = timezone.now()
    refund_request.rejection_reason = ""

    refund_request.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "rejection_reason",
        ]
    )

    return refund_request


@transaction.atomic
def reject_refund_request(
    refund_request,
    manager,
    rejection_reason,
):
    validate_pending(refund_request)

    if not manager:
        raise ValidationError("Manager is required.")

    if not manager.is_active:
        raise ValidationError("Rejecting manager is inactive.")

    if not rejection_reason or not rejection_reason.strip():
        raise ValidationError("Rejection reason is required.")

    refund_request.status = "REJECTED"
    refund_request.approved_by = manager
    refund_request.approved_at = timezone.now()
    refund_request.rejection_reason = rejection_reason.strip()

    refund_request.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "rejection_reason",
        ]
    )

    return refund_request

def get_refund_hotel(refund_request):
    if refund_request.source_type == "POS_ORDER":
        return refund_request.pos_order.department.hotel

    if refund_request.source_type == "CHARGE":
        return refund_request.charge.folio.hotel

    raise ValidationError("Invalid refund source.")


def validate_execution(refund_request, director):
    if refund_request.status != "APPROVED":
        raise ValidationError(
            "Only approved refund requests can be executed."
        )

    if not director:
        raise ValidationError("Director is required.")

    if not director.is_active:
        raise ValidationError(
            "Executing director is inactive."
        )

    validate_refund_request_source(refund_request)


def reverse_charge_accounting(
    charge,
    director,
):
    """
    Reverse accounting for a Charge where an original
    revenue posting exists.

    Room charges currently post:

        Dr Accounts Receivable
        Cr Room Revenue

    Therefore the refund posts:

        Dr Room Revenue
        Cr Accounts Receivable
    """

    if not charge.reference.startswith("ROOM-"):
        return

    hotel = charge.folio.hotel

    record_transaction(
        debit_system_key="room_revenue",
        credit_system_key="accounts_receivable",
        amount=charge.amount,
        description=f"Refund for Charge #{charge.id}",
        hotel=hotel,
        created_by=director,
        entry_type="REFUND",
        reference=f"CHARGE-REFUND-{charge.id}",
    )


def reverse_charge_payments(
    charge,
    director,
):
    """
    Reverse payments that have been applied to the folio.

    Normal payment accounting is:

        Dr Cash
        Cr Accounts Receivable

    Refund accounting is:

        Dr Accounts Receivable
        Cr Cash
    """

    from billing.models import Payment

    payments = (
        Payment.objects
        .select_for_update()
        .filter(
            folio=charge.folio,
            amount__gt=0,
        )
        .order_by("collected_at", "id")
    )

    remaining = charge.amount

    for payment in payments:
        if remaining <= 0:
            break

        refund_amount = min(
            remaining,
            payment.amount,
        )

        hotel = charge.folio.hotel

        cash = get_account(
            hotel,
            "1000",
        )

        receivable = get_account(
            hotel,
            "1100",
        )

        post_journal_entry(
            hotel=hotel,
            description=(
                f"Refund payment for Charge #{charge.id}"
            ),
            reference=(
                f"CHARGE-PAYMENT-REFUND-"
                f"{charge.id}-{payment.id}"
            ),
            created_by=director,
            lines=[
                {
                    "account": receivable,
                    "debit": refund_amount,
                },
                {
                    "account": cash,
                    "credit": refund_amount,
                },
            ],
        )

        Payment.objects.create(
            folio=charge.folio,
            amount=-refund_amount,
            method="REFUND",
            collected_by=director,
            reference=(
                f"CHARGE-PAYMENT-REFUND-"
                f"{charge.id}-{payment.id}"
            ),
            note=(
                f"Refund of payment #{payment.id} "
                f"for Charge #{charge.id}"
            ),
        )

        remaining -= refund_amount

    return charge.amount - remaining


def execute_charge_refund(
    refund_request,
    director,
):
    from billing.models import Charge

    charge = (
        Charge.objects
        .select_for_update()
        .select_related(
            "folio",
            "department",
        )
        .get(
            pk=refund_request.charge_id,
        )
    )

    if charge.amount <= 0:
        raise ValidationError(
            "Only positive charges can be refunded."
        )

    # Reverse accounting for charge types that have
    # an existing revenue posting.
    reverse_charge_accounting(
        charge,
        director,
    )

    # Reverse any payments actually collected against
    # this folio, up to the amount of this charge.
    refunded_payment_amount = reverse_charge_payments(
        charge,
        director,
    )

    # Reverse the folio charge itself.
    Charge.objects.create(
        folio=charge.folio,
        description=(
            f"Refund for Charge #{charge.id}"
        ),
        department=charge.department,
        amount=-charge.amount,
        reference=f"CHARGE-REFUND-{charge.id}",
    )

    return {
        "charge": charge,
        "refunded_payment_amount": refunded_payment_amount,
    }


@transaction.atomic
def execute_refund_request(
    refund_request,
    director,
):
    validate_execution(
        refund_request,
        director,
    )

    if refund_request.source_type == "POS_ORDER":
        from restaurant.workflows.refund_order import (
            refund_order,
        )

        refund_order(
            order=refund_request.pos_order,
            user=director,
            reason=refund_request.reason,
        )

    elif refund_request.source_type == "CHARGE":
        execute_charge_refund(
            refund_request,
            director,
        )

    else:
        raise ValidationError(
            "Unsupported refund source."
        )

    refund_request.status = "COMPLETED"
    refund_request.executed_by = director
    refund_request.executed_at = timezone.now()

    refund_request.save(
        update_fields=[
            "status",
            "executed_by",
            "executed_at",
        ]
    )

    return refund_request