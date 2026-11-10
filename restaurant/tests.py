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



class RefundShiftReconciliationTests(SimpleTestCase):
    @patch("restaurant.workflows.refund_order.Payment.objects.create")
    def test_refund_payment_is_linked_to_originating_shift_and_keeps_actual_payout_method(
        self, create_payment
    ):
        shift = SimpleNamespace(pk=31)
        folio = SimpleNamespace(pk=42)
        order = SimpleNamespace(
            status="PAID",
            id=77,
            total_amount=Decimal("1250.00"),
            folio=folio,
            shift=shift,
        )
        user = SimpleNamespace(pk=9)
        account = SimpleNamespace(pk=15)

        from restaurant.workflows.refund_order import reverse_payment

        reverse_payment(
            order,
            user,
            method="TRANSFER",
            account=account,
            reference="BANK-REFUND-778",
            note="Refund sent by bank transfer",
        )

        create_payment.assert_called_once_with(
            folio=folio,
            amount=Decimal("-1250.00"),
            method="TRANSFER",
            account=account,
            shift=shift,
            collected_by=user,
            reference="BANK-REFUND-778",
            note="Refund sent by bank transfer",
        )

    @patch("restaurant.workflows.refund_order.Payment.objects.create")
    def test_room_charge_refund_does_not_create_a_cash_payment(
        self, create_payment
    ):
        order = SimpleNamespace(status="CHARGED")

        from restaurant.workflows.refund_order import reverse_payment

        reverse_payment(order, SimpleNamespace(pk=9), method="CASH")

        create_payment.assert_not_called()


class PendingShiftHandoverPOSGuardTests(SimpleTestCase):
    databases = {"default"}

    @patch("core.services.operations.pos_flow.CashHandover.objects.filter")
    @patch("core.services.operations.pos_flow.Shift.objects.select_for_update")
    def test_pos_order_is_rejected_while_its_shift_handover_is_pending(
        self, select_for_update, handover_filter
    ):
        from core.services.operations.pos_flow import process_pos_order

        restaurant = SimpleNamespace(
            department_type="RESTAURANT",
            hotel_id=5,
            hotel=SimpleNamespace(pk=5),
        )
        user = SimpleNamespace(department=restaurant)
        shift = SimpleNamespace(pk=31)

        select_for_update.return_value.filter.return_value.first.return_value = shift
        handover_filter.return_value.exists.return_value = True

        with self.assertRaisesMessage(
            ValueError,
            "A cash handover is pending for this shift.",
        ):
            process_pos_order(
                user=user,
                cart={"items": {"12": {"qty": 1}}},
                settlement="PAY_NOW",
                payment_method="CASH",
            )

        select_for_update.return_value.filter.assert_called_once_with(
            user=user,
            department=restaurant,
            status="OPEN",
        )
        handover_filter.assert_called_once_with(
            shift=shift,
            from_user=user,
            status="PENDING",
        )

class SnackPOSInventoryRoutingTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Department, Hotel, Organization, Product
        from billing.models import Folio
        from restaurant.models import MenuItem, POSOrder, POSOrderItem

        self.User = get_user_model()
        organization = Organization.objects.create(name="Snack POS Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Snack POS Hotel")
        self.restaurant = Department.objects.create(
            hotel=self.hotel, code="SNR", name="Snack POS Restaurant", department_type="RESTAURANT"
        )
        self.kitchen = Department.objects.create(
            hotel=self.hotel, code="SNK", name="Snack POS Kitchen", department_type="KITCHEN"
        )
        self.user = self.User.objects.create_user(
            username="snack_pos_cashier", password="test-password",
            role="RESTAURANT", hotel=self.hotel, department=self.restaurant,
        )
        self.product = Product.objects.create(
            hotel=self.hotel, name="Samosa POS", sku="SNACK-POS-SAMOSA",
            product_type="SNACK", usage_type="RESALE", base_unit="pcs",
            price=Decimal("500.00"),
        )
        self.menu_item = MenuItem.objects.create(
            name="Samosa POS", product=self.product, price=Decimal("500.00")
        )
        self.folio = Folio.objects.create(folio_type="WALKIN", hotel=self.hotel)
        self.order = POSOrder.objects.create(
            department=self.restaurant, created_by=self.user, folio=self.folio,
        )
        POSOrderItem.objects.create(
            order=self.order, menu_item=self.menu_item, quantity=2, price=Decimal("500.00")
        )

    @patch("restaurant.models.stock_out")
    @patch("restaurant.models.Charge.objects.create")
    @patch("restaurant.models.KitchenTicket.objects.create")
    def test_snack_sale_deducts_restaurant_stock_without_creating_kitchen_ticket(
        self, create_ticket, create_charge, stock_out
    ):
        stock_out.return_value = {"total_cost": Decimal("250.00")}

        self.order.charge_order()

        stock_out.assert_called_once()
        self.assertEqual(stock_out.call_args.kwargs["department"], self.restaurant)
        create_ticket.assert_not_called()
        create_charge.assert_called_once()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "CHARGED")
        self.assertEqual(self.menu_item.category, "FOOD")
