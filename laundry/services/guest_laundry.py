from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import Charge
from billing.services.folio_factory import get_or_create_walkin_folio
from inventory.models import Department
from laundry.models import GuestLaundryOrder


def get_guest_laundry_walkin_folio(*, hotel):
    """
    Create a fresh WALKIN folio for a Guest Laundry walk-in customer.
    """

    department = Department.objects.filter(
        hotel=hotel,
        department_type="LAUNDRY",
        is_active=True,
    ).first()

    if not department:
        raise ValidationError(
            "No active Laundry department exists for this hotel."
        )

    return get_or_create_walkin_folio(department)



def create_guest_laundry_order(
    *,
    folio,
    description,
    quantity,
    unit_price,
    user,
    note="",
    customer_name="",
    customer_phone="",
):
    """
    Create a new Guest Laundry order with its first item.
    """

    if folio.folio_type not in ("ROOM", "WALKIN"):
        raise ValidationError(
            "Guest Laundry requires a room or walk-in folio."
        )

    if folio.is_closed:
        raise ValidationError(
            "Cannot create Guest Laundry for a closed folio."
        )

    if folio.folio_type == "ROOM":

        if not folio.guest:
            raise ValidationError(
                "The room folio has no guest."
            )

        if not folio.room:
            raise ValidationError(
                "The room folio has no room."
            )

    elif folio.folio_type == "WALKIN":

        if folio.room:
            raise ValidationError(
                "A walk-in folio cannot have a room."
            )

        customer_name = customer_name.strip()

        if not customer_name:
            raise ValidationError(
                "Walk-in customer name is required."
            )

        customer_phone = customer_phone.strip()

    description = description.strip()

    if not description:
        raise ValidationError(
            "Laundry item description is required."
        )

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Quantity must be a whole number."
        )

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    try:
        unit_price = Decimal(str(unit_price))
    except (TypeError, ValueError):
        raise ValidationError(
            "Unit price must be a valid amount."
        )

    if unit_price < Decimal("0.00"):
        raise ValidationError(
            "Unit price cannot be negative."
        )

    order = GuestLaundryOrder(
        folio=folio,
        customer_name=customer_name,
        customer_phone=customer_phone,
        status="NEW",
        note=note.strip(),
        created_by=user,
    )

    order.full_clean()
    order.save()

    item = order.items.create(
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        amount=unit_price * quantity,
    )

    order.recalculate_total()
    order.save(
        update_fields=["total_amount"],
    )

    return order, item

@transaction.atomic
def transition_guest_laundry_order(order_id, new_status):
    """
    Move a Guest Laundry order through its allowed workflow states.

    Allowed transitions:
        NEW -> RECEIVED
        RECEIVED -> PROCESSING
        PROCESSING -> READY
    """

    allowed_transitions = {
        "NEW": "RECEIVED",
        "RECEIVED": "PROCESSING",
        "PROCESSING": "READY",
    }

    order = (
        GuestLaundryOrder.objects
        .select_for_update()
        .get(pk=order_id)
    )

    expected_next_status = allowed_transitions.get(order.status)

    if expected_next_status != new_status:
        raise ValidationError(
            f"Cannot move Guest Laundry order from "
            f"{order.status} to {new_status}."
        )

    now = timezone.now()

    order.status = new_status

    if new_status == "RECEIVED":
        order.received_at = now

    elif new_status == "PROCESSING":
        order.processing_at = now

    elif new_status == "READY":
        order.ready_at = now

    order.save(
        update_fields=[
            "status",
            "received_at",
            "processing_at",
            "ready_at",
        ]
    )

    return order

@transaction.atomic
def cancel_guest_laundry_order(order_id):
    """
    Cancel a Guest Laundry order before processing begins.

    Cancellation is allowed only for NEW and RECEIVED orders.
    Once processing begins, the order cannot be cancelled.
    """

    order = (
        GuestLaundryOrder.objects
        .select_for_update()
        .get(pk=order_id)
    )

    if order.status not in ("NEW", "RECEIVED"):
        raise ValidationError(
            "Guest Laundry orders can only be cancelled "
            "before processing begins."
        )

    order.status = "CANCELLED"
    order.cancelled_at = timezone.now()

    order.save(
        update_fields=[
            "status",
            "cancelled_at",
        ]
    )

    return order

@transaction.atomic
def deliver_guest_laundry_order(order_id):
    """
    Deliver a Guest Laundry order and post its charge to the guest folio.

    Billing happens exactly once, at delivery.
    """

    order = (
        GuestLaundryOrder.objects
        .select_for_update()
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
        )
        .prefetch_related("items")
        .get(pk=order_id)
    )

    # Idempotency: already delivered and billed.
    if order.billing_charge_id:
        return order, order.billing_charge

    if order.status != "READY":
        raise ValidationError(
            "Only READY Guest Laundry orders can be delivered."
        )

    folio = order.folio

    if folio.folio_type not in ("ROOM", "WALKIN"):
        raise ValidationError(
            "Guest Laundry requires a room or walk-in folio."
        )

    if folio.is_closed:
        raise ValidationError(
            "Cannot deliver Guest Laundry for a closed folio."
        )

    if folio.folio_type == "ROOM":

        if not folio.guest:
            raise ValidationError(
                "The room folio has no guest."
            )

        if not folio.room:
            raise ValidationError(
                "The room folio has no room."
            )

    elif folio.folio_type == "WALKIN":

        if folio.room:
            raise ValidationError(
                "A walk-in folio cannot have a room."
            )

    items = list(order.items.all())

    if not items:
        raise ValidationError(
            "Cannot deliver a Guest Laundry order with no items."
        )

    # Recalculate from the item price snapshots.
    total = sum(
        (item.amount for item in items),
        Decimal("0.00"),
    )

    if total <= Decimal("0.00"):
        raise ValidationError(
            "Guest Laundry order total must be greater than zero."
        )

    order.total_amount = total

    department = folio.hotel.departments.filter(
        department_type="LAUNDRY",
        is_active=True,
    ).first()

    if not department:
        raise ValidationError(
            "No active Laundry department exists for this hotel."
        )

    charge = Charge.objects.create(
        folio=folio,
        description=f"Guest Laundry Order #{order.id}",
        department=department,
        amount=total,
        reference=f"LAUNDRY-{order.id}",
    )

    order.billing_charge = charge
    order.status = "DELIVERED"
    order.delivered_at = timezone.now()
    order.save(
        update_fields=[
            "total_amount",
            "billing_charge",
            "status",
            "delivered_at",
        ]
    )

    return order, charge