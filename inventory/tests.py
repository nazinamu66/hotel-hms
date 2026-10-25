from types import SimpleNamespace

from decimal import Decimal

from django.test import RequestFactory, SimpleTestCase, TestCase

from inventory.models import (
    Department,
    Hotel,
    Organization,
    Product,
    Stock,
    StockCostLayer,
    StockMovement,
    StockTransfer,
    Supplier,
)
from inventory.forms import PurchaseItemForm, PurchaseOrderForm
from inventory.views import po_submit, product_delete


class PurchaseOrderMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_cannot_submit_purchase_order(self):
        request = self.factory.get("/inventory/purchase-orders/123/submit/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="MANAGER",
        )

        response = po_submit(request, pk=123)

        self.assertEqual(response.status_code, 405)


class ProductArchiveMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_cannot_archive_product(self):
        request = self.factory.get("/inventory/products/123/delete/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="ADMIN",
        )

        response = product_delete(request, pk=123)

        self.assertEqual(response.status_code, 405)

class FractionalStockTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Fractional Stock Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Fractional Stock Test Hotel",
        )
        cls.store = Department.objects.create(
            hotel=cls.hotel,
            code="FST",
            name="Fractional Test Store",
            department_type="STORE",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="FKT",
            name="Fractional Test Kitchen",
            department_type="KITCHEN",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Fractional Rice",
            sku="FRACTIONAL-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )

    def test_fractional_transfer_preserves_physical_stock_and_fifo_layers(self):
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

        transfer = StockTransfer.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
            quantity=Decimal("1.250"),
            note="Fractional ingredient issue",
        )
        transfer.execute()

        source_stock = Stock.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_stock = Stock.objects.get(
            product=self.product,
            department=self.kitchen,
        )
        source_layer = StockCostLayer.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_layer = StockCostLayer.objects.get(
            product=self.product,
            department=self.kitchen,
        )
        movement = StockMovement.objects.get(reference=f"TRANSFER-{transfer.id}")

        self.assertEqual(source_stock.quantity, Decimal("8.750"))
        self.assertEqual(destination_stock.quantity, Decimal("1.250"))
        self.assertEqual(source_layer.quantity_remaining, Decimal("8.750"))
        self.assertEqual(destination_layer.quantity_remaining, Decimal("1.250"))
        self.assertEqual(movement.quantity, Decimal("1.250"))




class PurchaseOrderHotelIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="PO Isolation Org")
        cls.hotel_a = Hotel.objects.create(
            organization=organization,
            name="PO Isolation Hotel A",
        )
        cls.hotel_b = Hotel.objects.create(
            organization=organization,
            name="PO Isolation Hotel B",
        )
        cls.department_a = Department.objects.create(
            hotel=cls.hotel_a,
            code="POA",
            name="PO Department A",
            department_type="STORE",
        )
        cls.supplier_a = Supplier.objects.create(
            hotel=cls.hotel_a,
            name="Supplier A",
        )
        cls.supplier_b = Supplier.objects.create(
            hotel=cls.hotel_b,
            name="Supplier B",
        )
        cls.product_a = Product.objects.create(
            hotel=cls.hotel_a,
            name="Hotel A Product",
            sku="PO-ISO-A",
            product_type="RAW",
            base_unit="kg",
        )
        cls.product_b = Product.objects.create(
            hotel=cls.hotel_b,
            name="Hotel B Product",
            sku="PO-ISO-B",
            product_type="RAW",
            base_unit="kg",
        )

    def test_purchase_order_rejects_supplier_from_another_hotel(self):
        form = PurchaseOrderForm(
            data={
                "supplier": str(self.supplier_b.pk),
                "department": str(self.department_a.pk),
            },
            hotels=Hotel.objects.filter(
                pk__in=[self.hotel_a.pk, self.hotel_b.pk]
            ),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("supplier", form.errors)

    def test_purchase_item_form_excludes_products_from_other_hotels(self):
        form = PurchaseItemForm(hotel=self.hotel_a)

        self.assertTrue(
            form.fields["product"].queryset.filter(
                pk=self.product_a.pk
            ).exists()
        )
        self.assertFalse(
            form.fields["product"].queryset.filter(
                pk=self.product_b.pk
            ).exists()
        )
