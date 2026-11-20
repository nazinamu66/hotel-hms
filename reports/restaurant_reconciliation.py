from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from accounting.models import JournalEntry


CENT = Decimal("0.01")


def _money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _entry_account_movement(entries, system_key, *, debit_normal=True):
    """Return the signed movement on one system account across journal entries."""
    total = Decimal("0.00")
    for entry in entries:
        for line in entry.lines.all():
            if line.account.system_key != system_key:
                continue
            debit = Decimal(line.debit or 0)
            credit = Decimal(line.credit or 0)
            total += debit - credit if debit_normal else credit - debit
    return _money(total)


def _entry_debits(entries):
    return _money(sum(
        (Decimal(line.debit or 0) for entry in entries for line in entry.lines.all()),
        Decimal("0.00"),
    ))


def _status(expected, actual, entries, *, expected_entries=1, date_mismatch=False):
    if expected == 0 and not entries:
        return "NOT_REQUIRED"
    if not entries:
        return "MISSING"
    if len(entries) != expected_entries:
        return "DUPLICATE" if len(entries) > expected_entries else "MISSING"
    if _money(expected) != _money(actual):
        return "AMOUNT_MISMATCH"
    if date_mismatch:
        return "DATE_MISMATCH"
    return "RECONCILED"


def build_restaurant_accounting_reconciliation(
    *,
    revenue_orders,
    refund_charges,
    payments,
    complimentary_orders,
):
    """
    Compare Restaurant operational records with their existing journal entries.

    The report's operational date scope is retained. Sales posting dates are
    checked against each order's BusinessDay, not assumed to equal created_at.
    No journal entries or operational records are created or modified here.
    """
    revenue_orders = list(revenue_orders.select_related("business_day", "department__hotel"))
    complimentary_orders = list(complimentary_orders.select_related("business_day", "department__hotel"))
    refund_charges = list(refund_charges)
    payments = list(payments.select_related("folio"))

    hotel_ids = {
        order.department.hotel_id for order in revenue_orders + complimentary_orders
    }
    hotel_ids.update(charge.department.hotel_id for charge in refund_charges)
    hotel_ids.update(payment.folio.hotel_id for payment in payments)

    references = set()
    for order in revenue_orders + complimentary_orders:
        references.update({
            f"POS-{order.pk}",
            f"DRINK-COGS-{order.pk}",
            f"FOOD-COGS-{order.pk}",
            f"COGS-REV-DRINK-{order.pk}",
            f"COGS-REV-FOOD-{order.pk}",
            f"COMP-BREAKFAST-DRINK-COGS-{order.pk}",
            f"COMP-BREAKFAST-FOOD-COGS-{order.pk}",
        })
        references.update(
            JournalEntry.objects.none().values_list("reference", flat=True)
        )
    for charge in refund_charges:
        references.add(charge.reference)
    for payment in payments:
        references.add(f"PAY-{payment.pk}")
        if payment.reference.startswith("POS-") and payment.folio.folio_type == "WALKIN":
            references.add(payment.reference)

    # Refund spoilage reclassifications carry an item suffix, so include them
    # by querying the tightly scoped order-reference prefix separately below.
    entries = list(
        JournalEntry.objects.filter(
            hotel_id__in=hotel_ids,
            reference__in=references,
        ).prefetch_related("lines__account")
    ) if hotel_ids and references else []
    entries_by_reference = defaultdict(list)
    for entry in entries:
        entries_by_reference[entry.reference].append(entry)

    # Include POS refund spoilage reclassifications, whose references are
    # SPOILAGE-REFUND-<order id>-<item id>.
    refunded_ids = [order.pk for order in revenue_orders if order.is_refunded]
    if hotel_ids and refunded_ids:
        spoilage_entries = JournalEntry.objects.filter(
            hotel_id__in=hotel_ids,
            reference__startswith="SPOILAGE-REFUND-",
        ).filter(
            reference__in=[
                entry.reference for entry in JournalEntry.objects.none()
            ]
        )
        # Resolve the exact order prefix without scanning unrelated hotels'
        # journals; reference matching remains bounded to the report's hotels.
        spoilage_entries = JournalEntry.objects.filter(
            hotel_id__in=hotel_ids,
            reference__startswith="SPOILAGE-REFUND-",
        ).prefetch_related("lines__account")
        for entry in spoilage_entries:
            if any(entry.reference.startswith(f"SPOILAGE-REFUND-{order_id}-") for order_id in refunded_ids):
                entries_by_reference[entry.reference].append(entry)

    rows = []

    def add_row(*, group, label, reference, expected, actual, matched_entries,
                expected_entries=1, order_id=None, date_mismatch=False, note=""):
        expected = _money(expected)
        actual = _money(actual)
        rows.append({
            "group": group,
            "label": label,
            "reference": reference,
            "expected": expected,
            "actual": actual,
            "difference": _money(actual - expected),
            "status": _status(
                expected, actual, matched_entries,
                expected_entries=expected_entries,
                date_mismatch=date_mismatch,
            ),
            "entry_count": len(matched_entries),
            "order_id": order_id,
            "note": note,
        })

    # Revenue: the POS sale journal credits Restaurant Revenue.
    for order in revenue_orders:
        reference = f"POS-{order.pk}"
        matched = entries_by_reference.get(reference, [])
        expected = _money(order.total_amount)
        actual = _entry_account_movement(matched, "restaurant_revenue", debit_normal=False)
        expected_date = (
            order.business_day.date if order.business_day_id
            else order.created_at.date()
        )
        date_mismatch = bool(matched and any(entry.date != expected_date for entry in matched))
        add_row(
            group="Sales",
            label=f"POS order #{order.pk}",
            reference=reference,
            expected=expected,
            actual=actual,
            matched_entries=matched,
            order_id=order.pk,
            date_mismatch=date_mismatch,
            note=f"Expected business date: {expected_date}",
        )

    # Refunds: refund charges are the operational source for the amount.
    for charge in refund_charges:
        matched = entries_by_reference.get(charge.reference, [])
        expected = abs(Decimal(charge.amount))
        actual = _entry_account_movement(matched, "restaurant_revenue", debit_normal=True)
        order_id = None
        try:
            order_id = int(charge.reference.rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            pass
        add_row(
            group="Refunds",
            label=f"Refund {charge.reference}",
            reference=charge.reference,
            expected=expected,
            actual=actual,
            matched_entries=matched,
            order_id=order_id,
        )

    # COGS: compare the original category postings and the net COGS movement.
    # Refunds should fully reverse/reclassify the original COGS balance.
    for order in revenue_orders:
        drink_cost = Decimal("0.00")
        food_cost = Decimal("0.00")
        for item in order.items.select_related("menu_item__product"):
            product = item.menu_item.product
            if not product.is_stock_item():
                continue
            amount = Decimal(item.cost_at_sale or 0) * item.quantity
            if product.product_type == "DRINK":
                drink_cost += amount
            elif product.product_type in ("FOOD", "SNACK"):
                food_cost += amount
        drink_cost = _money(drink_cost)
        food_cost = _money(food_cost)
        initial_refs = [
            (f"DRINK-COGS-{order.pk}", drink_cost),
            (f"FOOD-COGS-{order.pk}", food_cost),
        ]
        initial_entries = []
        expected_initial = Decimal("0.00")
        actual_initial = Decimal("0.00")
        for reference, amount in initial_refs:
            if amount <= 0:
                continue
            matched = entries_by_reference.get(reference, [])
            initial_entries.extend(matched)
            expected_initial += amount
            actual_initial += _entry_account_movement(
                matched, "cost_of_goods_sold", debit_normal=True
            )
        add_row(
            group="COGS",
            label=f"Original COGS · order #{order.pk}",
            reference=f"DRINK/FOOD-COGS-{order.pk}",
            expected=expected_initial,
            actual=actual_initial,
            matched_entries=initial_entries,
            expected_entries=sum(1 for _, amount in initial_refs if amount > 0),
            order_id=order.pk,
        )

        net_refs = [
            f"DRINK-COGS-{order.pk}",
            f"FOOD-COGS-{order.pk}",
            f"COGS-REV-DRINK-{order.pk}",
            f"COGS-REV-FOOD-{order.pk}",
        ]
        net_entries = [
            entry for reference in net_refs
            for entry in entries_by_reference.get(reference, [])
        ]
        net_entries.extend(
            entry for reference, matches in entries_by_reference.items()
            if reference.startswith(f"SPOILAGE-REFUND-{order.pk}-")
            for entry in matches
        )
        net_actual = _entry_account_movement(
            net_entries, "cost_of_goods_sold", debit_normal=True
        )
        net_expected = Decimal("0.00") if order.is_refunded else expected_initial
        add_row(
            group="COGS",
            label=f"Net COGS after refunds · order #{order.pk}",
            reference=f"NET-COGS-{order.pk}",
            expected=net_expected,
            actual=net_actual,
            matched_entries=net_entries,
            expected_entries=1 if expected_initial > 0 else 0,
            order_id=order.pk,
            note="Refunded prepared food is reclassified to spoilage expense; unprepared items are reversed.",
        )

    # Complimentary breakfast costs debit Complimentary Meals Expense,
    # not Restaurant Revenue or ordinary Cost of Goods Sold.
    for order in complimentary_orders:
        expected = Decimal("0.00")
        refs = [
            f"COMP-BREAKFAST-DRINK-COGS-{order.pk}",
            f"COMP-BREAKFAST-FOOD-COGS-{order.pk}",
        ]
        matched = []
        for item in order.items.select_related("menu_item__product"):
            product = item.menu_item.product
            if product.is_stock_item():
                expected += Decimal(item.cost_at_sale or 0) * item.quantity
        for reference in refs:
            matched.extend(entries_by_reference.get(reference, []))
        actual = _entry_account_movement(
            matched, "complimentary_meals_expense", debit_normal=True
        )
        add_row(
            group="Complimentary",
            label=f"Complimentary breakfast · order #{order.pk}",
            reference=f"COMP-BREAKFAST-{order.pk}",
            expected=_money(expected),
            actual=actual,
            matched_entries=matched,
            expected_entries=sum(
                1 for reference in refs if entries_by_reference.get(reference)
            ) or (1 if expected > 0 else 0),
            order_id=order.pk,
            note="Cost is expensed to Complimentary Meals Expense; no guest revenue is recorded.",
        )

    # Shift-linked receipts: same-day walk-in POS payments are represented by
    # the combined POS sale journal; later room-charge collections use PAY-id.
    for payment in payments:
        if payment.reference.startswith("POS-") and payment.folio.folio_type == "WALKIN":
            reference = payment.reference
        else:
            reference = f"PAY-{payment.pk}"
        matched = entries_by_reference.get(reference, [])
        actual = _entry_debits(matched)
        add_row(
            group="Collections",
            label=f"Payment #{payment.pk} · {payment.get_method_display()}",
            reference=reference,
            expected=payment.amount,
            actual=actual,
            matched_entries=matched,
            order_id=None,
            note=f"Receipt reference: {payment.reference or '—'}",
        )

    discrepancy_statuses = {"MISSING", "DUPLICATE", "AMOUNT_MISMATCH", "DATE_MISMATCH"}
    for row in rows:
        row["is_discrepancy"] = row["status"] in discrepancy_statuses

    return {
        "rows": rows,
        "count": len(rows),
        "discrepancy_count": sum(row["is_discrepancy"] for row in rows),
        "reconciled_count": sum(row["status"] == "RECONCILED" for row in rows),
        "missing_count": sum(row["status"] == "MISSING" for row in rows),
        "difference_total": _money(sum(
            (row["difference"] for row in rows),
            Decimal("0.00"),
        )),
    }
