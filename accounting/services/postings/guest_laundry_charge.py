from django.core.exceptions import ValidationError

from accounting.services.journal import get_account, post_journal_entry


def post_guest_laundry_charge(order, charge):
    """
    Post a delivered Guest Laundry charge to Accounts Receivable and revenue.

    The order-specific journal reference makes repeat calls safe and also
    allows a previously delivered order with a missing journal to be repaired.
    """
    if charge.folio_id != order.folio_id:
        raise ValidationError(
            "Guest Laundry charge does not belong to this order's folio."
        )

    if charge.department.hotel_id != order.folio.hotel_id:
        raise ValidationError(
            "Guest Laundry charge department belongs to another hotel."
        )

    if charge.amount <= 0:
        raise ValidationError(
            "Guest Laundry charge amount must be greater than zero."
        )

    hotel = order.folio.hotel
    receivable = get_account(hotel, "1100")
    revenue = get_account(hotel, "4000")

    return post_journal_entry(
        hotel=hotel,
        description=f"Guest Laundry Order #{order.id}",
        reference=f"GUEST-LAUNDRY-{order.id}",
        created_by=order.created_by,
        lines=[
            {"account": receivable, "debit": charge.amount},
            {"account": revenue, "credit": charge.amount},
        ],
    )
