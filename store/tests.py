from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.contrib.auth import get_user_model

from inventory.models import (
    Department,
    Hotel,
    Organization,
    Product,
    Stock,
    StockCostLayer,
    StockMovement,
    StockTransfer,
)
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

    def test_store_ingredient_issue_rejects_get_requests(self):
        factory = RequestFactory()
        request = factory.get(f"/store/requests/item/1/issue/")
        request.user = self.store_user

        response = issue_request_item(request, item_id=1)

        self.assertEqual(response.status_code, 405)
