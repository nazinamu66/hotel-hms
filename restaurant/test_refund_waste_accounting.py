from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from restaurant.workflows.refund_order import record_refunded_food_waste


class FakeItems:
    def __init__(self, items):
        self.items = items

    def select_related(self, *args):
        return self.items


def make_item(product_type, unit_cost, quantity, *, stock_item=True, pk=1):
    product = SimpleNamespace(
        product_type=product_type,
        name=f"Test {product_type}",
        is_stock_item=lambda: stock_item,
    )
    return SimpleNamespace(
        pk=pk,
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(str(unit_cost)),
        quantity=Decimal(str(quantity)),
    )


def make_order(items, *, cogs_posted):
    return SimpleNamespace(
        id=42,
        items=FakeItems(items),
        is_cogs_posted=cogs_posted,
    )


class RefundedPreparedFoodWasteAccountingTests(SimpleTestCase):
    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_prepared_food_reclassifies_posted_cogs_to_spoilage(self, create_record, post):
        food = make_item("FOOD", "120.00", "1", pk=101)
        drink = make_item("DRINK", "30.00", "1", pk=102)
        order = make_order([food, drink], cogs_posted=True)
        kitchen = SimpleNamespace(hotel=object())
        create_record.return_value = SimpleNamespace(pk=7)

        record_refunded_food_waste(order, user=object(), kitchen=kitchen, reason="Customer complaint")

        create_record.assert_called_once()
        self.assertEqual(create_record.call_args.kwargs["cost_amount"], Decimal("120.00"))
        self.assertEqual(create_record.call_args.kwargs["quantity"], food.quantity)
        post.assert_called_once_with(
            debit_system_key="spoilage_expense",
            credit_system_key="cost_of_goods_sold",
            amount=Decimal("120.00"),
            description="Refunded POS Order #42 - prepared food spoilage #7 - Test FOOD",
            hotel=kitchen.hotel,
            created_by=order_user_from_record(create_record),
            entry_type="NORMAL",
            reference="SPOILAGE-REFUND-42-101",
        )

    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_prepared_food_without_posted_cogs_expenses_against_finished_goods(self, create_record, post):
        food = make_item("FOOD", "85.555", "2", pk=201)
        order = make_order([food], cogs_posted=False)
        kitchen = SimpleNamespace(hotel=object())
        create_record.return_value = SimpleNamespace(pk=8)

        record_refunded_food_waste(order, user="kitchen-user", kitchen=kitchen, reason="Prepared")

        self.assertEqual(create_record.call_args.kwargs["cost_amount"], Decimal("171.11"))
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["debit_system_key"], "spoilage_expense")
        self.assertEqual(post.call_args.kwargs["credit_system_key"], "finished_goods_inventory")
        self.assertEqual(post.call_args.kwargs["amount"], Decimal("171.11"))
        self.assertEqual(post.call_args.kwargs["reference"], "SPOILAGE-REFUND-42-201")

    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_zero_cost_food_creates_audit_record_but_no_journal(self, create_record, post):
        food = make_item("FOOD", "0", "1", pk=301)
        order = make_order([food], cogs_posted=True)
        create_record.return_value = SimpleNamespace(pk=9)

        record_refunded_food_waste(
            order, user="kitchen-user", kitchen=SimpleNamespace(hotel=object()), reason="Prepared"
        )

        create_record.assert_called_once()
        self.assertEqual(create_record.call_args.kwargs["cost_amount"], Decimal("0.00"))
        post.assert_not_called()


def order_user_from_record(create_record):
    return create_record.call_args.kwargs["recorded_by"]
