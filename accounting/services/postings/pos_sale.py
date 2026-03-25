from accounting.services.journal import record_transaction_by_slug


def post_pos_sale(order):
    """
    POS Sale → Debit Cash / Credit Revenue
    """

    hotel = order.department.hotel

    record_transaction_by_slug(
        source_slug="undeposited-funds",
        destination_slug="sales-revenue",
        amount=order.total_amount,
        description=f"POS Sale #{order.id}",
        hotel=hotel,
        created_by=order.created_by
    )