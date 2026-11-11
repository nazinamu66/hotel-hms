from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase, TestCase

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

class RefundedSnackWasteAccountingTests(SimpleTestCase):
    @patch("restaurant.workflows.refund_order.record_transaction")
    @patch("kitchen.models.KitchenSpoilageRecord.objects.create")
    def test_refunded_snack_waste_is_audited_under_restaurant_custody(
        self, create_record, record_transaction
    ):
        from restaurant.workflows.refund_order import record_refunded_food_waste

        kitchen = SimpleNamespace(hotel=SimpleNamespace())
        restaurant = SimpleNamespace(name="Restaurant")
        product = SimpleNamespace(
            product_type="SNACK",
            name="Samosa",
            is_stock_item=lambda: True,
        )
        item = SimpleNamespace(
            pk=91,
            menu_item=SimpleNamespace(product=product),
            cost_at_sale=Decimal("75.00"),
            quantity=2,
        )
        order = SimpleNamespace(
            id=92,
            is_cogs_posted=True,
            items=SimpleNamespace(select_related=lambda *args, **kwargs: [item]),
        )
        create_record.return_value = SimpleNamespace(pk=93)

        record_refunded_food_waste(
            order,
            SimpleNamespace(pk=9),
            kitchen,
            "Customer returned prepared snack",
            restaurant=restaurant,
        )

        self.assertIs(create_record.call_args.kwargs["department"], restaurant)
        self.assertEqual(create_record.call_args.kwargs["product"], product)
        self.assertEqual(create_record.call_args.kwargs["cost_amount"], Decimal("150.00"))
        self.assertEqual(
            record_transaction.call_args.kwargs["credit_system_key"],
            "cost_of_goods_sold",
        )


class RestaurantSpoilageApprovalTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Department, Hotel, Organization, Product, Stock
        from restaurant.models import RestaurantSpoilageRecord

        User = get_user_model()
        org = Organization.objects.create(name="Restaurant Spoilage Org")
        self.hotel = Hotel.objects.create(organization=org, name="Restaurant Spoilage Hotel")
        self.restaurant = Department.objects.create(
            hotel=self.hotel, code="RSP", name="Spoilage Restaurant",
            department_type="RESTAURANT",
        )
        self.cashier = User.objects.create_user(
            username="spoilage_cashier", password="test-password",
            role="RESTAURANT", hotel=self.hotel, department=self.restaurant,
        )
        self.manager = User.objects.create_user(
            username="spoilage_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        self.product = Product.objects.create(
            hotel=self.hotel, name="Cola Can", sku="SPOILAGE-COLA",
            product_type="DRINK", usage_type="RESALE", base_unit="can",
            price=Decimal("500.00"), cost_price=Decimal("250.00"),
        )
        self.stock = Stock.objects.create(
            product=self.product, department=self.restaurant, quantity=Decimal("12"),
        )
        self.Record = RestaurantSpoilageRecord

    def report_spoilage(self):
        self.client.force_login(self.cashier)
        return self.client.post("/restaurant/spoilage/", {
            "product": self.product.pk,
            "quantity": "2",
            "reason_code": "OPENED",
            "notes": "Can opened during service",
        })

    def test_report_stays_pending_without_stock_or_accounting_mutation(self):
        from unittest.mock import patch

        with patch("restaurant.views.stock_out") as stock_out:
            response = self.report_spoilage()
        self.assertEqual(response.status_code, 302)
        record = self.Record.objects.get()
        self.assertEqual(record.status, "PENDING")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("12"))
        stock_out.assert_not_called()
        self.assertIsNone(record.stock_movement_id)
        self.assertEqual(record.cost_amount, Decimal("0.00"))

    @patch("accounting.services.journal.post_journal_entry")
    @patch("accounting.services.journal.get_system_account")
    @patch("restaurant.views.stock_out")
    def test_manager_approval_deducts_stock_and_posts_spoilage_journal(
        self, stock_out_mock, get_account, post_journal
    ):
        from types import SimpleNamespace

        self.report_spoilage()
        record = self.Record.objects.get()
        movement = SimpleNamespace(pk=88)
        stock_out_mock.return_value = {
            "movement": movement,
            "total_cost": Decimal("500.00"),
            "uncosted_quantity": Decimal("0"),
        }
        expense = SimpleNamespace(pk=1)
        inventory = SimpleNamespace(pk=2)
        get_account.side_effect = [expense, inventory]
        journal = SimpleNamespace(pk=99)
        post_journal.return_value = journal

        self.client.force_login(self.manager)
        response = self.client.post(
            f"/restaurant/spoilage/{record.pk}/review/",
            {"decision": "APPROVE", "review_note": "Verified"},
        )

        self.assertEqual(response.status_code, 302)
        stock_out_mock.assert_called_once()
        self.assertEqual(stock_out_mock.call_args.kwargs["department"], self.restaurant)
        self.assertEqual(stock_out_mock.call_args.kwargs["quantity"], Decimal("2"))
        post_journal.assert_called_once()
        journal_kwargs = post_journal.call_args.kwargs
        self.assertEqual(journal_kwargs["reference"], f"RESTAURANT-SPOILAGE-{record.pk}")
        self.assertEqual(journal_kwargs["lines"][0]["debit"], Decimal("500.00"))
        self.assertEqual(journal_kwargs["lines"][1]["credit"], Decimal("500.00"))
        record.refresh_from_db()
        self.assertEqual(record.status, "APPROVED")
        self.assertEqual(record.cost_amount, Decimal("500.00"))
        self.assertEqual(record.stock_movement_id, movement.pk)
        self.assertEqual(record.journal_entry_id, journal.pk)

    def test_reporter_cannot_approve_own_report(self):
        from django.core.exceptions import PermissionDenied

        self.report_spoilage()
        record = self.Record.objects.get()
        self.client.force_login(self.cashier)
        response = self.client.post(
            f"/restaurant/spoilage/{record.pk}/review/",
            {"decision": "APPROVE"},
        )
        self.assertEqual(response.status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.status, "PENDING")

    def test_rejection_does_not_deduct_stock(self):
        from unittest.mock import patch

        self.report_spoilage()
        record = self.Record.objects.get()
        self.client.force_login(self.manager)
        with patch("restaurant.views.stock_out") as stock_out:
            response = self.client.post(
                f"/restaurant/spoilage/{record.pk}/review/",
                {"decision": "REJECT", "review_note": "Not sufficiently documented"},
            )
        self.assertEqual(response.status_code, 302)
        stock_out.assert_not_called()
        record.refresh_from_db()
        self.assertEqual(record.status, "REJECTED")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("12"))
