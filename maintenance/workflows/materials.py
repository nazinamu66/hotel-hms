from django.core.exceptions import ValidationError
from django.db import transaction
from accounting.services.postings.maintenance import (
    post_maintenance_material_consumption,
)

from inventory.models import (
    Department,
    Stock,
    stock_out,
)

def get_maintenance_department(hotel):

    """
    Return the active Maintenance department for this hotel.
    """

    return Department.objects.get(
        hotel=hotel,
        code="MNT",
        department_type="MAINTENANCE",
        is_active=True,
    )
    



def validate_material_product(product):
    """
    Maintenance materials must be operational/internal stock.
    """

    if not product.is_active:
        raise ValidationError(
            "This product is inactive."
        )

    if not product.is_operational_stock_item():
        raise ValidationError(
            "Only INTERNAL products can be used "
            "as Maintenance materials."
        )


def validate_material_quantity(quantity):

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )


@transaction.atomic
def consume_maintenance_material(
    ticket,
    product,
    quantity,
    user,
    description="",
):
    """
    Consume material from the hotel's Maintenance Store
    and attach the consumption to a MaintenanceTicket.

    Quantity is always expressed in the product's BASE UNIT.
    """

    # ---------------------------------------------------------
    # Ticket validation
    # ---------------------------------------------------------

    if ticket.status == "RESOLVED":
        raise ValidationError(
            "Materials cannot be added to a resolved ticket."
        )

    # ---------------------------------------------------------
    # Product validation
    # ---------------------------------------------------------

    validate_material_product(product)

    validate_material_quantity(quantity)

    # ---------------------------------------------------------
    # Determine Maintenance Store
    # ---------------------------------------------------------

    hotel = ticket.room.hotel

    maintenance_department = get_maintenance_department(
        hotel
    )

    # ---------------------------------------------------------
    # Product authorization
    # ---------------------------------------------------------

    if not product.departments.filter(
        id=maintenance_department.id
    ).exists():

        raise ValidationError(
            "Maintenance is not authorized "
            "to use this product."
        )

    # ---------------------------------------------------------
    # Check Maintenance Store stock
    # ---------------------------------------------------------

    stock = (
        Stock.objects
        .select_for_update()
        .filter(
            product=product,
            department=maintenance_department,
        )
        .first()
    )

    if not stock:
        raise ValidationError(
            "This product is not stocked "
            "in the Maintenance Store."
        )

    if stock.quantity < quantity:
        raise ValidationError(
            f"Insufficient Maintenance stock. "
            f"Available: {stock.quantity} "
            f"{product.base_unit}."
        )

    # ---------------------------------------------------------
    # Remove stock
    # ---------------------------------------------------------

    stock_out(
        product=product,
        department=maintenance_department,
        quantity=quantity,
        user=user,
        reason=f"Maintenance Ticket #{ticket.id}",
        reference=f"MAINT-{ticket.id}",
    )

    # ---------------------------------------------------------
    # Record material against ticket
    # ---------------------------------------------------------

    material = ticket.material_entries.create(
        product=product,
        source="STORE",
        quantity=quantity,
        unit_cost=product.cost_price,
        description=description,
        created_by=user,
    )

    # ---------------------------------------------------------
    # Post accounting entry
    #
    # Dr Maintenance Expense
    # Cr Inventory Asset
    # ---------------------------------------------------------

    post_maintenance_material_consumption(
        material=material,
        user=user,
    )

    return material