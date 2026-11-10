from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Stock, StockCostLayer
from inventory.workflows.cost_layers import allocate_fifo_layers
from kitchen.models import KitchenRestrictedStockLayer, KitchenRestrictedStockRecord


STOCK_PRODUCT_TYPES = ("RAW", "FOOD", "SNACK", "DRINK")


def _validate_quantity(value):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid quantity.")
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError("Quantity must be a finite number greater than zero.")
    return quantity


def _validate_product(product, department):
    if product.hotel_id != department.hotel_id:
        raise ValidationError("This product does not belong to the current hotel.")
    if product.product_type not in STOCK_PRODUCT_TYPES:
        raise ValidationError("Only raw ingredients, prepared food, snacks and drinks can be restricted.")


@transaction.atomic
def transfer_general_to_restricted(*, product, department, source_location, location, quantity, user, reason):
    """Move general Kitchen stock and its FIFO value into a restricted location."""
    quantity = _validate_quantity(quantity)
    _validate_product(product, department)

    if (
        source_location.department_id != department.pk
        or source_location.access_level != "SHARED"
        or not source_location.is_active
    ):
        raise ValidationError("Choose an active shared source location in this kitchen.")
    if (
        location.department_id != department.pk
        or location.access_level != "RESTRICTED"
        or not location.is_active
    ):
        raise ValidationError("Choose an active restricted destination in this kitchen.")

    stock = Stock.objects.select_for_update().filter(
        product=product, department=department
    ).first()
    if not stock or stock.quantity < quantity:
        raise ValidationError(f"Insufficient general kitchen stock for {product.name}.")

    allocations = allocate_fifo_layers(
        product=product, department=department, quantity=quantity
    )
    for allocation in allocations:
        source_layer = allocation["layer"]
        allocated = allocation["quantity"]
        source_layer.quantity_remaining -= allocated
        source_layer.save(update_fields=["quantity_remaining"])
        KitchenRestrictedStockLayer.objects.create(
            department=department,
            location=location,
            product=product,
            source_cost_layer=source_layer,
            source_reference=source_layer.source_reference,
            quantity_received=allocated,
            quantity_remaining=allocated,
            unit_cost=source_layer.unit_cost,
            received_at=source_layer.received_at,
        )

    stock.quantity -= quantity
    stock.save(update_fields=["quantity"])

    return KitchenRestrictedStockRecord.objects.create(
        department=department, record_type="TRANSFER",
        source_location=source_location, destination_location=location,
        product=product, item_name=product.name, quantity=quantity,
        unit=product.base_unit, reason=reason, recorded_by=user,
    )


@transaction.atomic
def release_restricted_to_general(*, product, department, location, destination, quantity, user, reason):
    """Release restricted FIFO stock back into ordinary department inventory."""
    quantity = _validate_quantity(quantity)
    _validate_product(product, department)

    if (
        location.department_id != department.pk
        or location.access_level != "RESTRICTED"
        or not location.is_active
    ):
        raise ValidationError("Choose an active restricted source location in this kitchen.")
    if (
        destination.department_id != department.pk
        or destination.access_level != "SHARED"
        or not destination.is_active
    ):
        raise ValidationError("Choose an active shared destination in this kitchen.")

    layers = list(
        KitchenRestrictedStockLayer.objects.select_for_update()
        .filter(
            department=department, location=location, product=product,
            quantity_remaining__gt=0,
        )
        .select_related("source_cost_layer")
        .order_by("received_at", "pk")
    )
    available = sum((layer.quantity_remaining for layer in layers), Decimal("0.000"))
    if available < quantity:
        raise ValidationError(
            f"Insufficient restricted stock for {product.name}. Available: {available} {product.base_unit}."
        )

    stock, _ = Stock.objects.select_for_update().get_or_create(
        product=product, department=department,
        defaults={"quantity": Decimal("0.000")},
    )
    remaining = quantity
    for layer in layers:
        if remaining <= 0:
            break
        allocated = min(layer.quantity_remaining, remaining)
        source_layer = layer.source_cost_layer
        StockCostLayer.objects.create(
            product=product,
            department=department,
            purchase_item=source_layer.purchase_item,
            source_reference=layer.source_reference,
            quantity_received=allocated,
            quantity_remaining=allocated,
            unit_cost=layer.unit_cost,
            is_opening_balance=source_layer.is_opening_balance,
            received_at=layer.received_at,
        )
        layer.quantity_remaining -= allocated
        layer.save(update_fields=["quantity_remaining"])
        remaining -= allocated

    stock.quantity += quantity
    stock.save(update_fields=["quantity"])

    return KitchenRestrictedStockRecord.objects.create(
        department=department, record_type="TRANSFER",
        source_location=location, destination_location=destination,
        product=product, item_name=product.name, quantity=quantity,
        unit=product.base_unit, reason=reason, recorded_by=user,
    )
