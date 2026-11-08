from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from inventory.models import (
    Department,
    Hotel,
    LowStockRequest,
    Organization,
    Product,
    Stock,
    StockCostLayer,
    StockMovement,
    StockTransfer,
)
from linen.models import LinenItem, LinenTransaction
from store.views import issue_request_item


class StoreStockTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Store Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Store Test Hotel",
        )
        cls.store = Department.objects.create(
            hotel=cls.hotel,
            code="STT",
            name="Test Store",
            department_type="STORE",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="SKT",
            name="Test Kitchen",
            department_type="KITCHEN",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Test Rice",
            sku="STORE-TEST-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )
        User = get_user_model()
        cls.store_user = User.objects.create_user(
            username="store-test-user",
            password="test-password",
            role="STORE",
            department=cls.store,
        )

    def setUp(self):
        Stock.objects.create(
            product=self.product,
            department=self.store,
            quantity=Decimal("10.000"),
        )
        StockCostLayer.objects.create(
            product=self.product,
            department=self.store,
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("100.0000"),
        )

    def test_stock_transfer_cannot_execute_twice(self):
        transfer = StockTransfer.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
            quantity=Decimal("1.250"),
            created_by=self.store_user,
        )

        transfer.execute()
        with self.assertRaises(ValidationError):
            transfer.execute()

        source_stock = Stock.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_stock = Stock.objects.get(
            product=self.product,
            department=self.kitchen,
        )
        self.assertEqual(source_stock.quantity, Decimal("8.750"))
        self.assertEqual(destination_stock.quantity, Decimal("1.250"))
        self.assertEqual(
            StockMovement.objects.filter(
                reference=f"TRANSFER-{transfer.id}",
                movement_type="TRANSFER",
            ).count(),
            1,
        )

    def test_linen_transfer_to_laundry_updates_linen_ledger_once(self):
        laundry = Department.objects.create(
            hotel=self.hotel,
            code="STL",
            name="Test Laundry",
            department_type="LAUNDRY",
        )
        linen_product = Product.objects.create(
            hotel=self.hotel,
            name="Test Bedsheet",
            sku="STORE-TEST-BEDSHEET",
            product_type="LAUNDRY",
            usage_type="ASSET",
            base_unit="piece",
            cost_price=Decimal("500.00"),
        )
        linen_item = LinenItem.objects.create(product=linen_product)
        Stock.objects.create(
            product=linen_product,
            department=self.store,
            quantity=Decimal("10.000"),
        )
        StockCostLayer.objects.create(
            product=linen_product,
            department=self.store,
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("500.0000"),
        )
        transfer = StockTransfer.objects.create(
            product=linen_product,
            from_department=self.store,
            to_department=laundry,
            quantity=Decimal("5.000"),
            created_by=self.store_user,
        )

        transfer.execute()

        entry = LinenTransaction.objects.get(
            linen_item=linen_item,
            reference=f"TRANSFER-{transfer.id}",
        )
        self.assertEqual(entry.quantity, 5)
        self.assertEqual(entry.event_type, "RECEIVED")
        self.assertEqual(entry.condition, "CLEAN")
        self.assertEqual(entry.to_location, "LAUNDRY")

        with self.assertRaises(ValidationError):
            transfer.execute()

        self.assertEqual(
            LinenTransaction.objects.filter(
                linen_item=linen_item,
                reference=f"TRANSFER-{transfer.id}",
            ).count(),
            1,
        )


    def test_general_stock_request_issue_preserves_fractional_quantity(self):
        housekeeping = Department.objects.create(
            hotel=self.hotel,
            code="SHK",
            name="Test Housekeeping",
            department_type="HOUSEKEEPING",
        )
        stock_request = LowStockRequest.objects.create(
            product=self.product,
            department=housekeeping,
            requested_quantity=2,
            fulfillment_type="STORE",
            requested_by=self.store_user,
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.post(
            reverse(
                "store_issue_stock_request",
                kwargs={"pk": stock_request.pk},
            ),
            {"quantity": "1.250"},
        )

        self.assertEqual(response.status_code, 302)

        transfer = StockTransfer.objects.get(
            stock_request=stock_request,
        )
        self.assertEqual(transfer.quantity, Decimal("1.250"))

        source_stock = Stock.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_stock = Stock.objects.get(
            product=self.product,
            department=housekeeping,
        )
        self.assertEqual(source_stock.quantity, Decimal("8.750"))
        self.assertEqual(destination_stock.quantity, Decimal("1.250"))

        stock_request.refresh_from_db()
        self.assertEqual(stock_request.status, "PARTIALLY_FULFILLED")

    def test_general_stock_request_rejects_more_than_three_decimal_places(self):
        housekeeping = Department.objects.create(
            hotel=self.hotel,
            code="SHK2",
            name="Test Housekeeping 2",
            department_type="HOUSEKEEPING",
        )
        stock_request = LowStockRequest.objects.create(
            product=self.product,
            department=housekeeping,
            requested_quantity=2,
            fulfillment_type="STORE",
            requested_by=self.store_user,
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.post(
            reverse(
                "store_issue_stock_request",
                kwargs={"pk": stock_request.pk},
            ),
            {"quantity": "1.2345"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StockTransfer.objects.filter(stock_request=stock_request).exists()
        )
        self.assertEqual(
            Stock.objects.get(
                product=self.product,
                department=self.store,
            ).quantity,
            Decimal("10.000"),
        )

    def test_store_ingredient_issue_rejects_get_requests(self):
        factory = RequestFactory()
        request = factory.get(f"/store/requests/item/1/issue/")
        request.user = self.store_user

        response = issue_request_item(request, item_id=1)

        self.assertEqual(response.status_code, 405)
