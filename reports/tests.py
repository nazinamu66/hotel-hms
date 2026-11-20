from django.test import TestCase

# Create your tests here.


from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.restaurant_reconciliation import build_restaurant_accounting_reconciliation


class _Rows(list):
    def select_related(self, *args, **kwargs):
        return self

    def prefetch_related(self, *args, **kwargs):
        return self


class _Lines(list):
    def all(self):
        return self


def _journal(reference, lines, *, entry_date=date(2026, 11, 20)):
    return SimpleNamespace(
        reference=reference,
        date=entry_date,
        lines=_Lines(lines),
    )


def _line(system_key, *, debit="0.00", credit="0.00"):
    return SimpleNamespace(
        account=SimpleNamespace(system_key=system_key),
        debit=Decimal(debit),
        credit=Decimal(credit),
    )


def _order(*, order_id=41, amount="100.00", cost="40.00"):
    product = SimpleNamespace(
        product_type="FOOD",
        is_stock_item=lambda: True,
    )
    item = SimpleNamespace(
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(cost),
        quantity=1,
    )
    hotel = SimpleNamespace(pk=7)
    department = SimpleNamespace(hotel_id=7, hotel=hotel)
    return SimpleNamespace(
        pk=order_id,
        total_amount=Decimal(amount),
        department=department,
        business_day_id=None,
        business_day=None,
        created_at=datetime(2026, 11, 20, 12, 0),
        is_refunded=False,
        items=_Rows([item]),
    )


class RestaurantAccountingReconciliationTests(SimpleTestCase):
    def _run(self, order, entries):
        with patch(
            "reports.restaurant_reconciliation.JournalEntry.objects.filter",
            return_value=_Rows(entries),
        ):
            return build_restaurant_accounting_reconciliation(
                revenue_orders=_Rows([order]),
                refund_charges=[],
                payments=[],
                complimentary_orders=_Rows(),
            )

    def test_matching_pos_sale_and_cogs_are_reconciled(self):
        order = _order()
        entries = [
            _journal("POS-41", [_line("cash", debit="100.00"), _line("restaurant_revenue", credit="100.00")]),
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        self.assertEqual(result["discrepancy_count"], 0)
        self.assertEqual(result["reconciled_count"], 3)
        self.assertTrue(all(row["status"] == "RECONCILED" for row in result["rows"]))

    def test_missing_sale_journal_is_flagged_without_creating_a_posting(self):
        order = _order()
        entries = [
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        sale_row = next(row for row in result["rows"] if row["group"] == "Sales")
        self.assertEqual(sale_row["status"], "MISSING")
        self.assertEqual(sale_row["expected"], Decimal("100.00"))
        self.assertEqual(sale_row["actual"], Decimal("0.00"))
        self.assertEqual(len(entries), 1)
