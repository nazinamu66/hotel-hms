from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from billing.workflows.refunds import execute_refund_request


class FakeItems:
    def __init__(self, items):
        self.items = items

    def select_related(self, *args):
        return self.items


class RefundKitchenGateTests(TestCase):
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


from decimal import Decimal

from django.contrib.auth import get_user_model

from accounting.models import Account, JournalEntry
from billing.models import Folio, RefundRequest
from inventory.models import Department, Hotel, Organization, Product, Stock
from kitchen.models import KitchenSpoilageRecord
from restaurant.models import MenuItem, POSOrder, POSOrderItem
from billing.workflows.refunds import confirm_kitchen_refund
from accounting.services.journal import record_transaction


class PreparedFoodRefundIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Prepared Refund Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Prepared Refund Test Hotel",
        )
        cls.restaurant = Department.objects.create(
            hotel=cls.hotel,
            code="PRR",
            name="Prepared Refund Restaurant",
            department_type="RESTAURANT",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="PRK",
            name="Prepared Refund Kitchen",
            department_type="KITCHEN",
        )
        cls.restaurant_user = User.objects.create_user(
            username="prepared_refund_cashier",
            password="test-password",
            role="RESTAURANT",
            hotel=cls.hotel,
            department=cls.restaurant,
        )
        cls.director = User.objects.create_user(
            username="prepared_refund_director",
            password="test-password",
            role="DIRECTOR",
            hotel=cls.hotel,
        )
        cls.kitchen_user = User.objects.create_user(
            username="prepared_refund_kitchen",
            password="test-password",
            role="KITCHEN",
            hotel=cls.hotel,
            department=cls.kitchen,
        )

        cls.food = Product.objects.create(
            hotel=cls.hotel,
            name="Prepared Refund Fried Rice",
            sku="PREP-REFUND-RICE",
            product_type="FOOD",
            usage_type="RESALE",
            base_unit="portion",
        )
        cls.menu_item = MenuItem.objects.create(
            name="Prepared Refund Fried Rice",
            product=cls.food,
            price=Decimal("3000.00"),
        )
        cls.folio = Folio.objects.create(
            hotel=cls.hotel,
            folio_type="WALKIN",
        )
        cls.order = POSOrder.objects.create(
            department=cls.restaurant,
            created_by=cls.restaurant_user,
            folio=cls.folio,
            total_amount=Decimal("3000.00"),
            status="CHARGED",
            is_cogs_posted=True,
        )
        cls.order_item = POSOrderItem.objects.create(
            order=cls.order,
            menu_item=cls.menu_item,
            quantity=1,
            price=Decimal("3000.00"),
            cost_at_sale=Decimal("2000.000000"),
        )

        # This is the remaining stock after the original POS charge.
        cls.kitchen_stock = Stock.objects.create(
            product=cls.food,
            department=cls.kitchen,
            quantity=Decimal("4.000"),
        )

        account_specs = [
            ("9500", "Prepared Refund Test COGS", "cogs", "cost_of_goods_sold"),
            ("9501", "Prepared Refund Test Spoilage", "expense", "spoilage_expense"),
            ("9521", "Prepared Refund Test Finished Goods", "inventory", "finished_goods_inventory"),
            ("9510", "Prepared Refund Test Receivable", "current_asset", "accounts_receivable"),
            ("9540", "Prepared Refund Test Revenue", "income", "restaurant_revenue"),
        ]
        for code, name, account_type, system_key in account_specs:
            account = Account.objects.filter(
                hotel=cls.hotel,
                system_key=system_key,
                is_system=True,
            ).first()
            if account:
                continue

            Account.objects.create(
                hotel=cls.hotel,
                code=code,
                name=name,
                account_type=account_type,
                system_key=system_key,
                is_system=True,
            )

    def test_prepared_waste_confirmation_posts_spoilage_and_does_not_restore_food_stock(self):
        record_transaction(
            debit_system_key="cost_of_goods_sold",
            credit_system_key="finished_goods_inventory",
            amount=Decimal("2000.00"),
            description=f"Food COGS for POS Order #{self.order.pk}",
            hotel=self.hotel,
            created_by=self.restaurant_user,
            entry_type="COGS",
            reference=f"FOOD-COGS-{self.order.pk}",
        )

        refund = RefundRequest.objects.create(
            source_type="POS_ORDER",
            pos_order=self.order,
            reason="Prepared food returned by guest",
            requested_by=self.restaurant_user,
            status="KITCHEN_CONFIRMATION",
            director_authorized_by=self.director,
        )

        completed = confirm_kitchen_refund(
            refund_request=refund,
            kitchen_user=self.kitchen_user,
            outcome="PREPARED_WASTE",
            note="Food was prepared and cannot be resold.",
        )

        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.kitchen_outcome, "PREPARED_WASTE")
        self.assertEqual(completed.kitchen_confirmed_by, self.kitchen_user)

        self.order.refresh_from_db()
        self.assertTrue(self.order.is_refunded)
        self.assertEqual(self.order.status, "CANCELLED")
        self.assertTrue(self.order.is_cogs_posted)

        self.kitchen_stock.refresh_from_db()
        self.assertEqual(self.kitchen_stock.quantity, Decimal("4.000"))

        spoilage = KitchenSpoilageRecord.objects.get(
            notes__contains=f"Refunded POS Order #{self.order.pk}"
        )
        self.assertEqual(spoilage.product, self.food)
        self.assertEqual(spoilage.quantity, Decimal("1.000"))
        self.assertEqual(spoilage.cost_amount, Decimal("2000.00"))
        self.assertIsNone(spoilage.stock_movement)

        original_cogs = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"FOOD-COGS-{self.order.pk}",
            entry_type="COGS",
        )
        self.assertEqual(
            sum(line.debit for line in original_cogs.lines.all()),
            Decimal("2000.00"),
        )

        journal = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"SPOILAGE-REFUND-{self.order.pk}-{self.order_item.pk}",
            entry_type="NORMAL",
        )
        lines = {
            line.account.system_key: (line.debit, line.credit)
            for line in journal.lines.select_related("account")
        }
        self.assertEqual(
            lines["spoilage_expense"],
            (Decimal("2000.00"), Decimal("0.00")),
        )
        self.assertEqual(
            lines["cost_of_goods_sold"],
            (Decimal("0.00"), Decimal("2000.00")),
        )

        # A completed request cannot be confirmed again to create duplicate waste.
        with self.assertRaisesMessage(Exception, "not awaiting Kitchen confirmation"):
            confirm_kitchen_refund(
                refund_request=completed,
                kitchen_user=self.kitchen_user,
                outcome="PREPARED_WASTE",
                note="Duplicate confirmation attempt",
            )

        self.assertEqual(
            KitchenSpoilageRecord.objects.filter(
                notes__contains=f"Refunded POS Order #{self.order.pk}"
            ).count(),
            1,
        )
