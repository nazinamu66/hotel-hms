from collections import defaultdict
from decimal import Decimal

from linen.models import LinenTransaction


ACTIVE_LOCATIONS = (
    "STORE",
    "HOUSEKEEPING",
    "LAUNDRY",
)


def get_linen_balance(linen_item):
    """
    Calculate the current physical balance of a LinenItem.

    Transactions are the source of truth.

    Returns quantities grouped by operational location/state.
    """

    balances = defaultdict(Decimal)

    transactions = (
        LinenTransaction.objects
        .filter(linen_item=linen_item)
        .order_by("created_at", "id")
    )

    for transaction in transactions:

        quantity = Decimal(transaction.quantity)

        event = transaction.event_type

        # --------------------------------------------------
        # LOCATION MOVEMENTS
        # --------------------------------------------------

        if event in (
            "RECEIVED",
            "ADJUSTMENT",
        ):

            if transaction.to_location:
                balances[
                    transaction.to_location
                ] += quantity

        elif event in (
            "ISSUED",
            "SENT_TO_LAUNDRY",
            "ISSUED_FROM_LAUNDRY",
            "RETURNED",
        ):

            if transaction.from_location:
                balances[
                    transaction.from_location
                ] -= quantity

            if transaction.to_location:
                balances[
                    transaction.to_location
                ] += quantity
        
        # --------------------------------------------------
        # CONDITION TRANSFORMATION
        # --------------------------------------------------

        elif event == "PROCESSED":
            # Linen remains at the same physical location.
            # Only its condition changes (DIRTY -> CLEAN).
            pass

        # --------------------------------------------------
        # LOSS / DAMAGE / WRITE-OFF
        # --------------------------------------------------

        elif event in (
            "DAMAGED",
            "LOST",
            "WRITTEN_OFF",
        ):

            if transaction.from_location:
                balances[
                    transaction.from_location
                ] -= quantity

            balances[event] += quantity

    return dict(balances)

def get_linen_condition_balance(linen_item, location):
    """
    Calculate current linen quantities by condition
    at a specific operational location.

    Returns:
        {
            "CLEAN": Decimal(...),
            "DIRTY": Decimal(...),
        }

    Transactions remain the source of truth.
    """

    balances = defaultdict(Decimal)

    transactions = (
        LinenTransaction.objects
        .filter(
            linen_item=linen_item,
        )
        .order_by("created_at", "id")
    )

    for transaction in transactions:

        quantity = Decimal(transaction.quantity)
        condition = transaction.condition
        event = transaction.event_type

        # --------------------------------------------------
        # LOCATION MOVEMENTS
        # --------------------------------------------------

        if event in (
            "RECEIVED",
            "ADJUSTMENT",
        ):

            if transaction.to_location == location:
                balances[condition] += quantity

        elif event in (
            "ISSUED",
            "SENT_TO_LAUNDRY",
            "ISSUED_FROM_LAUNDRY",
            "RETURNED",
        ):

            if transaction.from_location == location:
                balances[condition] -= quantity

            if transaction.to_location == location:
                balances[condition] += quantity
        

        # --------------------------------------------------
        # CONDITION TRANSFORMATION
        # --------------------------------------------------

        elif event == "PROCESSED":

            if transaction.from_location == location:
                balances["DIRTY"] -= quantity

            if transaction.to_location == location:
                balances["CLEAN"] += quantity

        # --------------------------------------------------
        # LOSS / DAMAGE / WRITE-OFF
        # --------------------------------------------------

        elif event in (
            "DAMAGED",
            "LOST",
            "WRITTEN_OFF",
        ):

            if transaction.from_location == location:
                balances[condition] -= quantity

    return {
        "CLEAN": balances["CLEAN"],
        "DIRTY": balances["DIRTY"],
    }

def get_linen_summary(linen_item):
    """
    Return a clean operational summary for a LinenItem.
    """

    balance = get_linen_balance(linen_item)

    store = balance.get(
        "STORE",
        Decimal("0"),
    )

    housekeeping = balance.get(
        "HOUSEKEEPING",
        Decimal("0"),
    )

    laundry = balance.get(
        "LAUNDRY",
        Decimal("0"),
    )

    damaged = balance.get(
        "DAMAGED",
        Decimal("0"),
    )

    lost = balance.get(
        "LOST",
        Decimal("0"),
    )

    written_off = balance.get(
        "WRITTEN_OFF",
        Decimal("0"),
    )

    owned = (
        store
        + housekeeping
        + laundry
        + damaged
        + lost
    )

    available = (
        store
        + housekeeping
        + laundry
    )

    return {
        "store": store,
        "housekeeping": housekeeping,
        "laundry": laundry,
        "damaged": damaged,
        "lost": lost,
        "written_off": written_off,
        "owned": owned,
        "available": available,
    }