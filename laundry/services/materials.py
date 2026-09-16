from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Department
from inventory.workflows.consume_stock import consume_stock

from laundry.models import (
    LaundryProcessing,
    LaundryProcessingMaterial,
)


@transaction.atomic
def consume_laundry_materials(
    *,
    processing,
    materials,
    user,
):
    """
    Consume the materials used by a Laundry processing event.

    Each material quantity is expressed in the product's BASE UNIT.
    Costs are determined by the inventory FIFO cost-layer engine.
    The caller never supplies a cost.

    materials must be an iterable of dictionaries containing:
        product
        quantity
    """

    if not isinstance(processing, LaundryProcessing):
        raise ValidationError(
            "A valid Laundry processing record is required."
        )

    if processing.hotel_id is None:
        raise ValidationError(
            "Laundry processing is not linked to a hotel."
        )

    laundry_department = Department.objects.filter(
        hotel_id=processing.hotel_id,
        department_type="LAUNDRY",
        is_active=True,
    ).first()

    if laundry_department is None:
        raise ValidationError(
            "No active Laundry department exists for this hotel."
        )

    materials = list(materials)

    if not materials:
        raise ValidationError(
            "At least one Laundry material is required."
        )

    total_cost = Decimal("0.00")
    material_records = []

    for material in materials:
        product = material.get("product")
        quantity = material.get("quantity")

        if product is None:
            raise ValidationError(
                "Every Laundry material must specify a product."
            )

        if product.hotel_id != processing.hotel_id:
            raise ValidationError(
                "Material product must belong to the same hotel."
            )

        if quantity is None:
            raise ValidationError(
                f"Quantity is required for {product.name}."
            )

        try:
            quantity = Decimal(str(quantity))
        except (TypeError, ValueError):
            raise ValidationError(
                f"Invalid quantity for {product.name}."
            )

        if quantity <= 0:
            raise ValidationError(
                f"Quantity for {product.name} must be greater than zero."
            )

        result = consume_stock(
            product=product,
            department=laundry_department,
            quantity=quantity,
            user=user,
            reason=(
                f"Laundry material consumption "
                f"for processing #{processing.id}"
            ),
            reference=(
                processing.reference
                or f"LAUNDRY-PROCESS-{processing.id}"
            ),
        )

        material_cost = Decimal(
            result["total_cost"]
        )

        movement = result["movement"]

        material_record = LaundryProcessingMaterial.objects.create(
            processing=processing,
            product=product,
            quantity=quantity,
            total_cost=material_cost,
            movement=movement,
        )

        material_records.append(material_record)
        total_cost += material_cost

    return {
        "processing": processing,
        "materials": material_records,
        "total_cost": total_cost,
    }