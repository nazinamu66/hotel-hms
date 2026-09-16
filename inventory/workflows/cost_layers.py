from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import StockCostLayer


def allocate_fifo_layers(
    *,
    product,
    department,
    quantity,
):
    """
    Allocate inventory quantity from the department's cost layers
    using FIFO.

    Quantity is always expressed in the product's BASE UNIT.

    Returns:
        list of dicts containing:
            layer
            quantity
            unit_cost
            total_cost

    Does NOT modify the layers.
    """

    quantity = Decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    layers = (
        StockCostLayer.objects
        .select_for_update()
        .filter(
            product=product,
            department=department,
            quantity_remaining__gt=0,
        )
        .order_by("received_at", "id")
    )

    remaining = quantity
    allocations = []

    for layer in layers:
        if remaining <= 0:
            break

        allocated = min(
            layer.quantity_remaining,
            remaining,
        )

        allocations.append(
            {
                "layer": layer,
                "quantity": allocated,
                "unit_cost": layer.unit_cost,
                "total_cost": allocated * layer.unit_cost,
            }
        )

        remaining -= allocated

    if remaining > 0:
        raise ValidationError(
            f"Insufficient cost-layer stock for {product.name} "
            f"in {department.name}. "
            f"Missing {remaining} {product.base_unit}."
        )

    return allocations


@transaction.atomic
def transfer_cost_layers(
    *,
    product,
    from_department,
    to_department,
    quantity,
):
    """
    Move FIFO cost layers from one department to another.

    Physical Stock quantities are NOT changed here.
    This function only moves the historical cost value.

    The source layers are consumed FIFO and equivalent destination
    layers are created at the same historical unit costs.

    Returns:
        allocations used for the transfer.
    """

    quantity = Decimal(quantity)

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    if from_department == to_department:
        raise ValidationError(
            "Source and destination cannot be the same."
        )

    if from_department.hotel_id != to_department.hotel_id:
        raise ValidationError(
            "Cross-hotel transfers are not allowed."
        )

    allocations = allocate_fifo_layers(
        product=product,
        department=from_department,
        quantity=quantity,
    )

    for allocation in allocations:
        layer = allocation["layer"]
        allocated = allocation["quantity"]

        layer.quantity_remaining -= allocated
        layer.save(
            update_fields=["quantity_remaining"]
        )

        StockCostLayer.objects.create(
            product=product,
            department=to_department,
            purchase_item=layer.purchase_item,
            quantity_received=allocated,
            quantity_remaining=allocated,
            unit_cost=layer.unit_cost,
            is_opening_balance=layer.is_opening_balance,
        )

    return allocations

@transaction.atomic
def consume_cost_layers(
    *,
    product,
    department,
    quantity,
):
    """
    Consume inventory from a department using FIFO cost layers.

    Quantity is always expressed in the product's BASE UNIT.

    The layers are reduced in FIFO order.

    Returns:
        {
            "allocations": [...],
            "total_cost": Decimal(...),
        }

    Does NOT change physical Stock.quantity.
    Physical stock deduction is handled by the inventory
    consumption workflow so both operations can be kept atomic.
    """

    quantity = Decimal(quantity)

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    allocations = allocate_fifo_layers(
        product=product,
        department=department,
        quantity=quantity,
    )

    total_cost = Decimal("0.00")

    for allocation in allocations:
        layer = allocation["layer"]
        allocated = allocation["quantity"]

        layer.quantity_remaining -= allocated

        layer.save(
            update_fields=["quantity_remaining"]
        )

        total_cost += allocation["total_cost"]

    return {
        "allocations": allocations,
        "total_cost": total_cost,
    }