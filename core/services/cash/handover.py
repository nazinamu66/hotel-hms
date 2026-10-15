from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services.access import (
    user_can_access_department,
    user_can_access_hotel,
)
from core.models import CashHandover


RECIPIENT_ROLES = {
    "RESTAURANT",
    "MANAGER",
    "ACCOUNTANT",
}


def _validate_same_scope(from_user, to_user, department):
    if from_user == to_user:
        raise ValidationError("A cash handover recipient must be different from the sender.")

    if not user_can_access_department(from_user, department):
        raise ValidationError("Sender cannot access this department.")

    if not user_can_access_hotel(to_user, department.hotel):
        raise ValidationError("Recipient does not have access to this hotel.")

    if to_user.role not in RECIPIENT_ROLES:
        raise ValidationError("Recipient must be a Cashier, Manager, or Accountant.")

    if to_user.role == "RESTAURANT" and to_user.department_id != department.id:
        raise ValidationError(
            "A cashier receiving a shift handover must belong to the same department."
        )


@transaction.atomic
def create_handover(
    *,
    from_user,
    to_user,
    department,
    expected_amount,
    sender_count,
    shift=None,
    custody_type="CUSTODY",
):
    """
    Create a pending physical-cash handover.

    This records custody only. It does not create any accounting entry.
    """

    _validate_same_scope(from_user, to_user, department)

    if custody_type not in {"SHIFT", "CUSTODY"}:
        raise ValidationError("Invalid custody type.")

    expected_amount = Decimal(expected_amount)
    sender_count = Decimal(sender_count)

    if expected_amount < 0:
        raise ValidationError("Expected cash cannot be negative.")

    if sender_count < 0:
        raise ValidationError("Sender count cannot be negative.")

    if custody_type == "SHIFT":
        if from_user.role != "RESTAURANT":
            raise ValidationError(
                "A shift handover must be initiated by a cashier."
            )

        if to_user.role != "RESTAURANT":
            raise ValidationError(
                "A shift handover recipient must be another cashier."
            )

        if shift is None:
            raise ValidationError("A shift handover must be linked to an open shift.")

        if shift.user_id != from_user.id:
            raise ValidationError("The shift does not belong to the sender.")

        if shift.department_id != department.id:
            raise ValidationError("The shift department does not match the handover.")

        if shift.status != "OPEN":
            raise ValidationError("Only an open shift can be handed over.")

    if custody_type == "CUSTODY" and shift is not None:
        if from_user.role != "RESTAURANT":
            raise ValidationError(
                "A custody handover linked to a shift must be initiated by a cashier."
            )

        if shift.user_id != from_user.id:
            raise ValidationError(
                "The shift does not belong to the sender."
            )

        if shift.department_id != department.id:
            raise ValidationError(
                "The shift department does not match the handover."
            )

        if shift.status != "OPEN":
            raise ValidationError(
                "Only an open shift can be handed over."
            )

    return CashHandover.objects.create(
        hotel=department.hotel,
        department=department,
        from_user=from_user,
        to_user=to_user,
        shift=shift,
        custody_type=custody_type,
        expected_amount=expected_amount,
        sender_count=sender_count,
        status="PENDING",
    )


@transaction.atomic
def accept_handover(*, handover_id, recipient, recipient_count, variance_note=""):
    """
    Recipient independently counts and accepts a pending cash handover.

    A handover is accepted only when:
        1. The sender's count matches the expected amount.
        2. The recipient's count matches the sender's count.

    Any difference records VARIANCE and requires an explanation.
    """

    handover = (
        CashHandover.objects
        .select_for_update()
        .select_related(
            "hotel",
            "department",
            "from_user",
            "to_user",
            "shift",
        )
        .get(id=handover_id)
    )

    if handover.status != "PENDING":
        raise ValidationError("This cash handover is no longer pending.")

    if handover.to_user_id != recipient.id:
        raise ValidationError("You are not the recipient of this handover.")

    if not user_can_access_hotel(recipient, handover.hotel):
        raise ValidationError("You do not have access to this hotel.")

    recipient_count = Decimal(recipient_count)

    if recipient_count < 0:
        raise ValidationError("Recipient count cannot be negative.")

    # --------------------------------------------------
    # Variance calculations
    # --------------------------------------------------
    sender_variance = (
        handover.sender_count
        - handover.expected_amount
    )

    custody_variance = (
        recipient_count
        - handover.sender_count
    )

    # Keep the existing variance field as the
    # recipient-vs-sender custody variance for compatibility.
    variance = custody_variance

    has_variance = (
        sender_variance != 0
        or custody_variance != 0
    )

    if has_variance and not variance_note.strip():
        raise ValidationError(
            "A variance explanation is required when the counted cash "
            "does not match the expected or sender amount."
        )

    handover.recipient_count = recipient_count
    handover.sender_variance = sender_variance
    handover.custody_variance = custody_variance
    handover.variance = variance

    if not has_variance:
        handover.status = "ACCEPTED"
        handover.variance_note = ""
        handover.accepted_at = timezone.now()
    else:
        handover.status = "VARIANCE"
        handover.variance_note = variance_note.strip()
        handover.accepted_at = None

    handover.save(
        update_fields=[
            "recipient_count",
            "sender_variance",
            "custody_variance",
            "variance",
            "status",
            "variance_note",
            "accepted_at",
        ]
    )

    return handover


@transaction.atomic
def cancel_handover(*, handover_id, user):
    """
    Cancel a pending handover before recipient acceptance.
    """

    handover = (
        CashHandover.objects
        .select_for_update()
        .get(id=handover_id)
    )

    if handover.status != "PENDING":
        raise ValidationError("Only pending handovers can be cancelled.")

    if handover.from_user_id != user.id:
        raise ValidationError("Only the sender can cancel this handover.")

    handover.status = "CANCELLED"
    handover.save(update_fields=["status"])

    return handover

@transaction.atomic
def accept_handover_and_transition(
    *,
    handover_id,
    recipient,
    recipient_count,
    variance_note="",
):
    """
    Accept a cash handover.

    For a matching SHIFT handover:
        - close the sender's shift
        - open a new shift for the recipient
        - carry accepted cash into opening_cash

    For a matching CUSTODY handover linked to a shift:
        - close the sender's shift
        - recipient takes physical cash custody
        - recipient does not get a restaurant shift

    For a general CUSTODY handover without a shift:
        - record custody only
        - no shift is opened or closed

    Variances are recorded but do not transition the shift.
    """

    handover = (
        CashHandover.objects
        .select_for_update()
        .select_related(
            "hotel",
            "department",
            "from_user",
            "to_user",
            "shift",
        )
        .get(id=handover_id)
    )

    # Use the existing acceptance rules.
    handover = accept_handover(
        handover_id=handover.id,
        recipient=recipient,
        recipient_count=recipient_count,
        variance_note=variance_note,
    )

    # A variance is not a completed custody transition.
    if handover.status != "ACCEPTED":
        return handover, None

    # General custody with no source shift does not affect shifts.
    if handover.shift is None:
        return handover, None

    shift = handover.shift

    if shift.status != "OPEN":
        raise ValidationError(
            "The source shift is no longer open."
        )

    # Close the sender's shift using the sender's physical count.
    shift.closing_cash = handover.sender_count
    shift.closed_at = timezone.now()
    shift.status = "CLOSED"
    shift.save(
        update_fields=[
            "closing_cash",
            "closed_at",
            "status",
        ]
    )

    # Cashier-to-manager/accountant:
    # custody changes, but the recipient does not open a restaurant shift.
    if handover.custody_type == "CUSTODY":
        return handover, None

    # SHIFT handover:
    # recipient must not already have another open shift.
    from restaurant.models import Shift

    existing_shift = (
        Shift.objects
        .select_for_update()
        .filter(
            user=recipient,
            department=handover.department,
            status="OPEN",
        )
        .first()
    )

    if existing_shift:
        raise ValidationError(
            "The recipient already has an open shift in this department."
        )

    # Start the recipient's new cashier shift.
    new_shift = Shift.objects.create(
        user=recipient,
        department=handover.department,
        opening_cash=handover.recipient_count,
        status="OPEN",
    )

    return handover, new_shift

@transaction.atomic
def resolve_cash_variance(
    *,
    handover_id,
    resolved_by,
    responsible_user_id,
    resolution_amount,
    resolution_note="",
):
    """
    Management resolves a cash variance after reviewing the handover.

    The resolution records that the responsible party has made good
    the variance. Recovery of the physical money is handled outside
    the system.

    No accounting journal is created here.
    """

    handover = (
        CashHandover.objects
        .select_for_update()
        .select_related(
            "hotel",
            "department",
            "from_user",
            "to_user",
        )
        .get(id=handover_id)
    )

    if handover.status != "VARIANCE":
        raise ValidationError(
            "Only cash handovers with an unresolved variance can be resolved."
        )

    if not user_can_access_hotel(resolved_by, handover.hotel):
        raise ValidationError(
            "You do not have access to this hotel."
        )

    if resolved_by.role not in {
        "MANAGER",
        "ACCOUNTANT",
        "DIRECTOR",
        "ADMIN",
    }:
        raise ValidationError(
            "Only management can resolve a cash variance."
        )

    if responsible_user_id not in {
        handover.from_user_id,
        handover.to_user_id,
    }:
        raise ValidationError(
            "The responsible party must be the sender or recipient "
            "of this handover."
        )

    resolution_amount = Decimal(resolution_amount)

    if resolution_amount <= 0:
        raise ValidationError(
            "Resolution amount must be greater than zero."
        )

    # Determine the actual outstanding variance.
    outstanding_variance = max(
        abs(handover.sender_variance),
        abs(handover.custody_variance),
    )

    if resolution_amount != outstanding_variance:
        raise ValidationError(
            f"Resolution amount must equal the outstanding variance "
            f"of ₦{outstanding_variance:.2f}."
        )

    if not resolution_note.strip():
        raise ValidationError(
            "A resolution note is required."
        )

    handover.responsible_user_id = responsible_user_id
    handover.resolution_type = "PAID_BACK"
    handover.resolution_amount = resolution_amount
    handover.resolved_by = resolved_by
    handover.resolved_at = timezone.now()
    handover.resolution_note = resolution_note.strip()
    handover.status = "RESOLVED"

    handover.save(
        update_fields=[
            "responsible_user",
            "resolution_type",
            "resolution_amount",
            "resolved_by",
            "resolved_at",
            "resolution_note",
            "status",
        ]
    )

    # --------------------------------------------------
    # Complete Restaurant SHIFT transition after variance
    # resolution.
    # --------------------------------------------------
    if handover.custody_type == "SHIFT" and handover.shift_id:

        from restaurant.models import Shift

        shift = (
            Shift.objects
            .select_for_update()
            .get(id=handover.shift_id)
        )

        if shift.status != "OPEN":
            raise ValidationError(
                "The source Restaurant shift is no longer open."
            )

        resolved_cash = handover.expected_amount

        shift.closing_cash = resolved_cash
        shift.closed_at = timezone.now()
        shift.status = "CLOSED"

        shift.save(
            update_fields=[
                "closing_cash",
                "closed_at",
                "status",
            ]
        )

        existing_shift = (
            Shift.objects
            .select_for_update()
            .filter(
                user=handover.to_user,
                department=handover.department,
                status="OPEN",
            )
            .first()
        )

        if existing_shift:
            raise ValidationError(
                "The recipient already has an open Restaurant shift."
            )

        Shift.objects.create(
            user=handover.to_user,
            department=handover.department,
            opening_cash=resolved_cash,
            status="OPEN",
        )

    return handover

    

