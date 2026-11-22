from collections import defaultdict
from decimal import Decimal

from django.db import models

from linen.models import (
    CleaningLinenSubmission,
    LinenCustody,
    LinenItem,
    LinenTransaction,
    RoomLinenState,
)
from linen.services.balances import get_linen_summary


ZERO = Decimal("0")


def build_linen_reconciliation(*, hotels):
    """
    Build a read-only reconciliation between the linen transaction ledger,
    employee custody and room-level physical state.

    Employee custody is a subledger of Housekeeping, not additional owned
    stock. Room state is compared with the separate ROOMS ledger balance.
    This function never writes or adjusts records.
    """
    items = (
        LinenItem.objects.filter(
            product__hotel__in=hotels,
            product__is_active=True,
            is_active=True,
        )
        .select_related("product", "product__hotel")
        .order_by("product__hotel__name", "product__name")
    )

    rows = []
    totals = defaultdict(lambda: ZERO)

    for item in items:
        summary = get_linen_summary(item)

        custody_rows = list(
            LinenCustody.objects.filter(
                linen_item=item,
                user__department__hotel=item.product.hotel,
                user__department__department_type="HOUSEKEEPING",
            )
            .select_related("user", "user__department")
            .order_by("user__username")
        )
        # Keep custody records for staff whose department was changed or removed
        # visible as an exception; do not silently count them as valid HK custody.
        all_custody_rows = list(
            LinenCustody.objects.filter(linen_item=item)
            .filter(
                models.Q(user__department__isnull=True)
                | models.Q(user__department__hotel=item.product.hotel)
            )
            .select_related("user", "user__department", "user__department__hotel")
            .order_by("user__username")
        )
        valid_custody_ids = {row.pk for row in custody_rows}
        unlinked_custody_rows = [
            row for row in all_custody_rows if row.pk not in valid_custody_ids
        ]
        unlinked_custody_total = sum(
            (Decimal(row.clean_quantity + row.dirty_quantity) for row in unlinked_custody_rows),
            ZERO,
        )
        custody_clean = sum((Decimal(row.clean_quantity) for row in custody_rows), ZERO)
        custody_dirty = sum((Decimal(row.dirty_quantity) for row in custody_rows), ZERO)
        custody_total = custody_clean + custody_dirty

        room_rows = list(
            RoomLinenState.objects.filter(
                linen_item=item,
                room__hotel=item.product.hotel,
            )
            .select_related("room", "room__hotel")
            .order_by("room__room_number")
        )
        room_state_total = sum((Decimal(row.quantity) for row in room_rows), ZERO)
        transactions = list(
            LinenTransaction.objects.filter(linen_item=item)
            .select_related("performed_by", "from_user", "to_user", "from_room", "to_room")
            .order_by("-created_at", "-id")[:100]
        )
        custody_movements = [
            movement for movement in transactions
            if movement.from_user_id or movement.to_user_id
        ]
        recent_submissions = (
            CleaningLinenSubmission.objects.filter(
                assignment__room__hotel=item.product.hotel,
            )
            .select_related("assignment", "assignment__room", "submitted_by")
            .order_by("-submitted_at", "-id")[:100]
        )
        cleaning_submissions = [
            submission for submission in recent_submissions
            if any(
                entry.get("linen_item_id") == item.pk
                for entry in submission.snapshot
                if isinstance(entry, dict)
            )
        ]
        room_ledger = summary["rooms"]
        room_variance = room_state_total - room_ledger

        housekeeping_ledger = summary["housekeeping"]
        unassigned_housekeeping = housekeeping_ledger - custody_total

        has_issue = (
            room_variance != ZERO
            or unassigned_housekeeping < ZERO
            or bool(unlinked_custody_rows)
        )
        row = {
            "linen_item": item,
            "hotel": item.product.hotel,
            "summary": summary,
            "custody_rows": custody_rows,
            "unlinked_custody_rows": unlinked_custody_rows,
            "unlinked_custody_total": unlinked_custody_total,
            "custody_movements": custody_movements,
            "cleaning_submissions": cleaning_submissions,
            "custody_clean": custody_clean,
            "custody_dirty": custody_dirty,
            "custody_total": custody_total,
            "housekeeping_ledger": housekeeping_ledger,
            "unassigned_housekeeping": unassigned_housekeeping,
            "room_rows": room_rows,
            "transactions": transactions,
            "room_state_total": room_state_total,
            "room_ledger": room_ledger,
            "room_variance": room_variance,
            "has_issue": has_issue,
        }
        rows.append(row)

        for key in (
            "store", "housekeeping", "laundry", "rooms", "in_transit",
            "damaged", "lost", "written_off", "owned",
        ):
            totals[key] += summary[key]
        totals["custody"] += custody_total
        totals["unassigned_housekeeping"] += unassigned_housekeeping
        totals["room_state"] += room_state_total
        totals["room_variance"] += room_variance
        if has_issue:
            totals["items_with_variances"] += 1

    return {"rows": rows, "totals": dict(totals)}
