from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction

from inventory.models import Product, Stock
from inventory.workflows.consume_stock import consume_stock

from housekeeping.models import CleaningAssignment


def validate_assignment(assignment, user):
    """
    A housekeeper may only record materials against
    their own active cleaning assignment.
    """

    if not assignment:
        raise ValidationError(
            "Cleaning assignment not found."
        )

    if assignment.assigned_to_id != user.id:
        raise PermissionDenied(
            "You can only record materials for your own "
            "cleaning assignments."
        )

    if assignment.status not in (
        "ASSIGNED",
        "IN_PROGRESS",
    ):
        raise ValidationError(
            "Materials can only be recorded for an "
            "active cleaning assignment."
        )


def validate_material_product(product, department):
    """
    Housekeeping materials must be active INTERNAL
    products authorized for the Housekeeping department.
    """

    if not product.is_active:
        raise ValidationError(
            "This product is inactive."
        )

    if not product.is_operational_stock_item():
        raise ValidationError(
            "Only INTERNAL products can be used "
            "as Housekeeping materials."
        )

    if not product.departments.filter(
        id=department.id,
    ).exists():
        raise PermissionDenied(
            "Housekeeping is not authorized "
            "to use this product."
        )


def validate_quantity(quantity):
    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )


@transaction.atomic
def consume_cleaning_material(
    assignment,
    product,
    quantity,
    user,
    notes="",
):
    """
    Consume Housekeeping stock against a cleaning assignment.

    Quantity is always expressed in the product's BASE UNIT.
    """

    # ---------------------------------------------------------
    # Assignment validation
    # ---------------------------------------------------------

    validate_assignment(
        assignment,
        user,
    )

    # ---------------------------------------------------------
    # Determine Housekeeping department
    # ---------------------------------------------------------

    hotel = assignment.room.hotel

    housekeeping_department = hotel.departments.filter(
        department_type="HOUSEKEEPING",
        is_active=True,
    ).first()

    if not housekeeping_department:
        raise ValidationError(
            "No active Housekeeping department exists "
            "for this hotel."
        )

    # ---------------------------------------------------------
    # Product validation
    # ---------------------------------------------------------

    validate_material_product(
        product,
        housekeeping_department,
    )

    validate_quantity(
        quantity,
    )

    # ---------------------------------------------------------
    # Check stock before consuming
    # ---------------------------------------------------------

    stock = (
        Stock.objects
        .select_for_update()
        .filter(
            product=product,
            department=housekeeping_department,
        )
        .first()
    )

    if not stock:
        raise ValidationError(
            "This product is not stocked "
            "in the Housekeeping Store."
        )

    if stock.quantity < quantity:
        raise ValidationError(
            f"Insufficient Housekeeping stock. "
            f"Available: {stock.quantity} "
            f"{product.base_unit}."
        )

    # ---------------------------------------------------------
    # Consume stock
    # ---------------------------------------------------------

    consume_stock(
        product=product,
        department=housekeeping_department,
        quantity=quantity,
        user=user,
        reason=(
            f"Housekeeping cleaning "
            f"Room {assignment.room.room_number}"
        ),
        reference=f"CLEAN-{assignment.id}",
    )

    # ---------------------------------------------------------
    # Record usage against cleaning assignment
    # ---------------------------------------------------------

    usage = assignment.material_usages.create(
        product=product,
        quantity=quantity,
        notes=notes,
        recorded_by=user,
    )

    return usage
