from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import SupplierLedger
from accounting.services.journal import record_transaction


def _get_maintenance_ticket_hotel(ticket):
    """Resolve the hotel for either a room-based or location-based ticket."""
    if ticket.room_id:
        return ticket.room.hotel
    if ticket.location_id:
        return ticket.location.hotel
    raise ValidationError(
        "Maintenance ticket must belong to a room or maintenance location."
    )


def post_maintenance_material_consumption(
    material,
    user,
):
    """
    Post Maintenance Store material consumption.

    Accounting:
        Debit  Maintenance Expense
        Credit Inventory Asset
    """

    if material.source != "STORE":
        raise ValueError(
            "Only STORE maintenance materials can be "
            "posted as inventory consumption."
        )

    hotel = _get_maintenance_ticket_hotel(material.ticket)

    amount = Decimal(material.quantity * material.unit_cost)

    if amount <= 0:
        return None

    return record_transaction(
        debit_system_key="maintenance_expense",
        credit_system_key="inventory_asset",
        amount=amount,
        description=(
            f"Maintenance material consumed "
            f"for Ticket #{material.ticket.id}"
        ),
        hotel=hotel,
        created_by=user,
        entry_type="MAINTENANCE",
        reference=(
            f"MAINT-{material.ticket.id}"
            f"-MAT-{material.id}"
        ),
    )


@transaction.atomic
def post_maintenance_labour(
    labour,
    user=None,
):
    """
    Post an outsourced maintenance labour charge.

    Accounting:
        Debit  Maintenance Expense
        Credit Accounts Payable

    Also records the supplier liability
    in the supplier sub-ledger.
    """

    if not labour:
        raise ValidationError(
            "Maintenance labour entry is required."
        )

    if labour.labour_type != "OUTSOURCED":
        raise ValidationError(
            "Only outsourced maintenance labour "
            "can be posted to supplier accounting."
        )

    if not labour.supplier:
        raise ValidationError(
            "Outsourced maintenance labour requires "
            "a supplier."
        )

    amount = Decimal(str(labour.amount))

    if amount <= 0:
        raise ValidationError(
            "Outsourced maintenance labour amount "
            "must be greater than zero."
        )

    hotel = _get_maintenance_ticket_hotel(labour.ticket)

    # Defensive hotel validation.
    if labour.supplier.hotel_id != hotel.id:
        raise ValidationError(
            "Supplier does not belong to the "
            "maintenance ticket's hotel."
        )

    journal_reference = (
        f"MAINT-{labour.ticket_id}"
        f"-LAB-{labour.id}"
    )

    journal_entry = record_transaction(
        debit_system_key="maintenance_expense",
        credit_system_key="accounts_payable",
        amount=amount,
        description=(
            f"Outsourced maintenance labour "
            f"for Ticket #{labour.ticket_id}"
        ),
        hotel=hotel,
        created_by=user or labour.created_by,
        entry_type="MAINTENANCE",
        reference=journal_reference,
    )

    SupplierLedger.objects.get_or_create(
        supplier=labour.supplier,
        journal_entry=journal_entry,
        defaults={
            "amount": amount,
            "entry_type": "credit",
        },
    )

    return journal_entry
