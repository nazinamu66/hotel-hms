from accounting.services.journal import post_journal_entry, get_account


def post_room_charge(folio, amount, user, reference=None):
    """Post room revenue using a charge-specific journal reference.

    The legacy fallback remains for older callers.
    """
    hotel = folio.hotel
    receivable = get_account(hotel, "1100")
    revenue = get_account(hotel, "4000")
    post_journal_entry(
        hotel=hotel,
        description=f"Room charge folio #{folio.id}",
        reference=reference or f"ROOM-{folio.room.room_number}",
        created_by=user,
        lines=[
            {"account": receivable, "debit": amount},
            {"account": revenue, "credit": amount},
        ],
    )
