from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from accounting.services.postings.cogs import post_cogs_for_order
from accounting.services.postings.cogs_reversal import reverse_cogs_for_order


class FakeItems:
    def __init__(self, items):
        self._items = items

    def select_related(self, *args):
        return self._items


def make_item(product_type, unit_cost, quantity, name=None):
    product = SimpleNamespace(
        product_type=product_type,
        name=name or product_type.title(),
        is_stock_item=lambda: True,
        id=1 if product_type == "DRINK" else 2,
    )
    return SimpleNamespace(
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(str(unit_cost)),
        quantity=quantity,
    )


def make_order(items, cogs_posted=False):
    return SimpleNamespace(
        id=123,
        department=SimpleNamespace(hotel=object()),
        created_by=None,
        items=FakeItems(items),
        is_cogs_posted=cogs_posted,
        save=lambda **kwargs: None,
    )


class POSCOGSCurrencyPrecisionTests(SimpleTestCase):
    @patch("accounting.services.postings.cogs.record_transaction")
    def test_cogs_rounds_category_totals_once_to_currency_precision(self, record):
        order = make_order([
            make_item("DRINK", "1.234567", 3, "Soda"),
            make_item("FOOD", "2.345678", 2, "Meal"),
        ])

        result = post_cogs_for_order(order)

        self.assertEqual(result["drink_cogs"], Decimal("3.70"))
        self.assertEqual(result["food_cogs"], Decimal("4.69"))
        self.assertEqual(record.call_count, 2)
        amounts = {call.kwargs["reference"]: call.kwargs["amount"] for call in record.call_args_list}
        self.assertEqual(amounts["DRINK-COGS-123"], Decimal("3.70"))
        self.assertEqual(amounts["FOOD-COGS-123"], Decimal("4.69"))

    @patch("accounting.services.postings.cogs.record_transaction")
    def test_cogs_rejects_missing_historical_unit_cost(self, record):
        order = make_order([make_item("FOOD", "0", 1, "Meal")])

        with self.assertRaisesRegex(ValueError, "Missing historical cost"):
            post_cogs_for_order(order)

        record.assert_not_called()

    @patch("accounting.services.postings.cogs_reversal.record_transaction")
    def test_refund_reverses_same_rounded_category_totals(self, record):
        order = make_order([
            make_item("DRINK", "1.234567", 3, "Soda"),
            make_item("FOOD", "2.345678", 2, "Meal"),
        ], cogs_posted=True)

        result = reverse_cogs_for_order(order)

        self.assertEqual(result["drink_cogs"], Decimal("3.70"))
        self.assertEqual(result["food_cogs"], Decimal("4.69"))
        self.assertEqual(record.call_count, 2)
        amounts = {call.kwargs["reference"]: call.kwargs["amount"] for call in record.call_args_list}
        self.assertEqual(amounts["COGS-REV-DRINK-123"], Decimal("3.70"))
        self.assertEqual(amounts["COGS-REV-FOOD-123"], Decimal("4.69"))
        self.assertFalse(order.is_cogs_posted)
