from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from restaurant.views import cart_clear


class CartClearMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def make_request(self, method):
        request = getattr(self.factory, method.lower())("/restaurant/cart/clear/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="RESTAURANT",
        )
        request.session = {
            "pos_cart": {
                "items": {"12": {"name": "Tea", "qty": 2}},
                "charge_type": "WALKIN",
                "room_id": None,
            }
        }
        return request

    def test_get_cannot_clear_cart(self):
        response = cart_clear(self.make_request("GET"))

        self.assertEqual(response.status_code, 405)

    def test_post_clears_cart(self):
        request = self.make_request("POST")

        response = cart_clear(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            request.session["pos_cart"],
            {"items": {}, "charge_type": "WALKIN", "room_id": None},
        )


from decimal import Decimal
from unittest.mock import patch

from restaurant.workflows.refund_order import record_refunded_food_waste


class RefundedFoodWasteAccountingTests(SimpleTestCase):
    def make_order(self, *, cogs_posted, unit_cost="120.00"):
        product = SimpleNamespace(
            product_type="FOOD",
            name="Jollof Rice",
            is_stock_item=lambda: True,
        )
        item = SimpleNamespace(
            pk=17,
            menu_item=SimpleNamespace(product=product),
            cost_at_sale=Decimal(unit_cost),
            quantity=Decimal("2"),
        )
        return SimpleNamespace(
            id=44,
            is_cogs_posted=cogs_posted,
            items=SimpleNamespace(
                select_related=lambda *args, **kwargs: [item],
            ),
        )

    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_prepared_waste_reclassifies_posted_food_cogs_to_spoilage(
        self, create_record, record_transaction
    ):
        create_record.return_value = SimpleNamespace(pk=8)
        order = self.make_order(cogs_posted=True)

        record_refunded_food_waste(
            order, SimpleNamespace(pk=1), SimpleNamespace(hotel=SimpleNamespace()),
            "Customer complaint",
        )

        kwargs = record_transaction.call_args.kwargs
        self.assertEqual(kwargs["debit_system_key"], "spoilage_expense")
        self.assertEqual(kwargs["credit_system_key"], "cost_of_goods_sold")
        self.assertEqual(kwargs["amount"], Decimal("240.00"))
        self.assertEqual(kwargs["reference"], "SPOILAGE-REFUND-44-17")
        self.assertEqual(
            create_record.call_args.kwargs["cost_amount"], Decimal("240.00")
        )

    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_prepared_waste_without_posted_cogs_credits_finished_goods_inventory(
        self, create_record, record_transaction
    ):
        create_record.return_value = SimpleNamespace(pk=9)
        order = self.make_order(cogs_posted=False)

        record_refunded_food_waste(
            order, SimpleNamespace(pk=1), SimpleNamespace(hotel=SimpleNamespace()),
            "Prepared and wasted",
        )

        kwargs = record_transaction.call_args.kwargs
        self.assertEqual(kwargs["debit_system_key"], "spoilage_expense")
        self.assertEqual(kwargs["credit_system_key"], "finished_goods_inventory")
        self.assertEqual(kwargs["amount"], Decimal("240.00"))

    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_zero_cost_waste_record_does_not_post_zero_value_journal(
        self, create_record, record_transaction
    ):
        create_record.return_value = SimpleNamespace(pk=10)
        order = self.make_order(cogs_posted=True, unit_cost="0.00")

        record_refunded_food_waste(
            order, SimpleNamespace(pk=1), SimpleNamespace(hotel=SimpleNamespace()),
            "No cost available",
        )

        record_transaction.assert_not_called()
