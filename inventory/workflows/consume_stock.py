from django.core.exceptions import (
    ValidationError,
    PermissionDenied,
)

from inventory.models import stock_out


def validate_product(product):
    if not product.is_active:
        raise ValidationError(
            "This product is inactive."
        )

    if not product.is_operational_stock_item():
        raise ValidationError(
            "Only INTERNAL products can be consumed "
            "as operational stock."
        )


def validate_department(department):
    if not department.is_active:
        raise ValidationError(
            "This department is inactive."
        )

    if not department.hotel_id:
        raise ValidationError(
            "Department is not linked to a hotel."
        )


def validate_product_department(
    product,
    department,
):
    if not product.departments.filter(
        id=department.id,
    ).exists():

        raise PermissionDenied(
            "This department is not authorized "
            "to use this product."
        )


def validate_quantity(quantity):
    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )


def consume_stock(
    product,
    department,
    quantity,
    user,
    reason="",
    reference="",
):
    """
    Consume INTERNAL operational stock.

    quantity is always expressed in the product's
    BASE UNIT.
    """

    validate_product(product)

    validate_department(
        department,
    )

    validate_product_department(
        product,
        department,
    )

    validate_quantity(
        quantity,
    )

    stock_out(
        product=product,
        department=department,
        quantity=quantity,
        user=user,
        reason=reason,
        reference=reference,
    )