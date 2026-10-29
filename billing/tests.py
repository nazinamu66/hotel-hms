from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from billing.workflows.refunds import execute_refund_request


class FakeItems:
    def __init__(self, items):
        self.items = items

    def select_related(self, *args):
        return self.items


class RefundKitchenGateTests(SimpleTestCase):
    @patch("billing.workflows.refunds.validate_execution")
    @patch("restaurant.workflows.refund_order.refund_order")
    def test_food_order_waits_for_kitchen_after_director_authorization(self, refund_order, validate):
        food = SimpleNamespace(menu_item=SimpleNamespace(product=SimpleNamespace(product_type="FOOD")))
        order = SimpleNamespace(items=FakeItems([food]), kitchen_ticket=object())
        request = SimpleNamespace(
            status="APPROVED",
            source_type="POS_ORDER",
            pos_order=order,
            pos_order_id=123,
            charge_id=None,
            reason="Guest complaint",
            save=lambda **kwargs: None,
            director_authorized_by=None,
            director_authorized_at=None,
        )
        director = SimpleNamespace(is_active=True)

        result = execute_refund_request(request, director)

        self.assertIs(result, request)
        self.assertEqual(request.status, "KITCHEN_CONFIRMATION")
        self.assertIs(request.director_authorized_by, director)
        self.assertIsNotNone(request.director_authorized_at)
        refund_order.assert_not_called()

