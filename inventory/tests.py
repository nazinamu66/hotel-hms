from types import SimpleNamespace

from decimal import Decimal

from django.db import models
from django.core.exceptions import ValidationError

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from unittest.mock import patch

from inventory.models import (
    Department,
    Hotel,
    Organization,
    Product,
    PurchaseItem,
    PurchaseOrder,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Stock,
    StockCostLayer,
    StockMovement,
    StockTransfer,
    Supplier,
)
from inventory.forms import ProductForm, PurchaseItemForm, PurchaseOrderForm
from inventory.views import po_submit, product_delete
from inventory.services.costing import get_latest_cost
from accounts.services.access import can_create_product
from kitchen.models import ProductionBatch, Recipe


User = get_user_model()


class PreparedFoodProductFormTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Prepared Food Form Test Org"
        )
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Prepared Food Form Test Hotel",
        )
        self.kitchen, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Kitchen",
            defaults={
                "code": "KIT",
                "department_type": "KITCHEN",
            },
        )

    def form_data(self, **overrides):
        data = {
            "name": "Fried Rice",
            "sku": "FRIED-RICE-FORM",
            "barcode": "",
            "product_type": "FOOD",
            "base_unit": "portion",
            "purchase_unit": "portion",
            "unit_multiplier": "1",
            "supply_source": "STORE",
            "departments": [str(self.kitchen.pk)],
            "usage_type": "RESALE",
            "reorder_level": "0",
            "price": "2500.00",
            "purchase_cost": "0.00",
            "register_as_linen": "",
            "linen_par_level": "0",
            "linen_minimum_level": "0",
        }
        data.update(overrides)
        return data

    def test_prepared_food_does_not_require_supplier_purchase_cost(self):
        form = ProductForm(
            data=self.form_data(),
            hotel=self.hotel,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["usage_type"], "RESALE")
        self.assertEqual(form.cleaned_data["purchase_cost"], Decimal("0.00"))

    def test_drink_resale_still_requires_purchase_cost(self):
        form = ProductForm(
            data=self.form_data(
                name="Bottled Water",
                sku="BOTTLED-WATER-FORM",
                product_type="DRINK",
                base_unit="bottle",
                purchase_unit="carton",
                unit_multiplier="24",
            ),
            hotel=self.hotel,
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Resale products must have a purchase cost.",
            form.non_field_errors(),
        )


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

class KitchenIngredientDepartmentAssignmentTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Kitchen Assignment Test Org"
        )
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Kitchen Assignment Test Hotel",
        )
        # Hotel creation provisions standard departments automatically.
        # Reuse those rows instead of violating the per-hotel name constraint.
        self.kitchen, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Kitchen",
            defaults={
                "code": "KIT",
                "department_type": "KITCHEN",
            },
        )
        self.store, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Store",
            defaults={
                "code": "STR",
                "department_type": "STORE",
            },
        )
        self.housekeeping, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Housekeeping",
            defaults={
                "code": "HK",
                "department_type": "HOUSEKEEPING",
            },
        )
        self.kitchen_head = User.objects.create_user(
            username="kitchen-assignment-head",
            password="test-password",
            role="KITCHEN",
            hotel=self.hotel,
            department=self.kitchen,
            is_department_head=True,
        )

    def test_kitchen_head_can_assign_raw_ingredient_to_store(self):
        self.assertTrue(
            can_create_product(
                self.kitchen_head,
                self.store,
                product_type="RAW",
            )
        )

    def test_kitchen_head_cannot_assign_non_raw_product_to_store(self):
        self.assertFalse(
            can_create_product(
                self.kitchen_head,
                self.store,
                product_type="FOOD",
            )
        )

    def test_kitchen_head_cannot_assign_raw_ingredient_to_other_department(self):
        self.assertFalse(
            can_create_product(
                self.kitchen_head,
                self.housekeeping,
                product_type="RAW",
            )
        )

    def test_kitchen_head_can_assign_product_to_own_kitchen(self):
        self.assertTrue(
            can_create_product(
                self.kitchen_head,
                self.kitchen,
                product_type="RAW",
            )
        )


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



class KitchenScopedLatestCostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Kitchen Cost Scope Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Kitchen Cost Scope Hotel",
        )
        cls.kitchen_a = Department.objects.create(
            hotel=cls.hotel,
            code="KCA",
            name="Cost Scope Kitchen A",
            department_type="KITCHEN",
        )
        cls.kitchen_b = Department.objects.create(
            hotel=cls.hotel,
            code="KCB",
            name="Cost Scope Kitchen B",
            department_type="KITCHEN",
        )
        cls.user_a = User.objects.create_user(
            username="cost_scope_kitchen_a",
            password="test-password",
            role="KITCHEN",
            hotel=cls.hotel,
            department=cls.kitchen_a,
        )
        cls.user_b = User.objects.create_user(
            username="cost_scope_kitchen_b",
            password="test-password",
            role="KITCHEN",
            hotel=cls.hotel,
            department=cls.kitchen_b,
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Cost Scope Jollof Rice",
            sku="COST-SCOPE-JOLLOF",
            product_type="FOOD",
            usage_type="RESALE",
            base_unit="portion",
        )
        cls.recipe = Recipe.objects.create(
            name="Cost Scope Jollof Recipe",
            product=cls.product,
        )

    def test_latest_cost_uses_only_the_requested_kitchen(self):
        batch_a = ProductionBatch.objects.create(
            recipe=self.recipe,
            quantity_produced=10,
            produced_by=self.user_a,
            is_executed=True,
        )
        batch_b = ProductionBatch.objects.create(
            recipe=self.recipe,
            quantity_produced=10,
            produced_by=self.user_b,
            is_executed=True,
        )

        def cost_for_batch(batch):
            return {
                batch_a.pk: Decimal("12.00"),
                batch_b.pk: Decimal("25.00"),
            }[batch.pk]

        with patch.object(ProductionBatch, "cost_per_unit", autospec=True, side_effect=cost_for_batch):
            self.assertEqual(get_latest_cost(self.product, self.kitchen_a), Decimal("12.00"))
            self.assertEqual(get_latest_cost(self.product, self.kitchen_b), Decimal("25.00"))

    def test_latest_cost_returns_zero_when_requested_kitchen_has_no_batch(self):
        ProductionBatch.objects.create(
            recipe=self.recipe,
            quantity_produced=10,
            produced_by=self.user_a,
            is_executed=True,
        )

        self.assertEqual(get_latest_cost(self.product, self.kitchen_b), Decimal("0.00"))



class PurchaseReceiptLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Receipt Ledger Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Receipt Ledger Hotel",
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="RLS",
            name="Receipt Ledger Store",
            department_type="STORE",
        )
        cls.supplier = Supplier.objects.create(
            hotel=cls.hotel,
            name="Receipt Ledger Supplier",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Receipt Ledger Rice",
            sku="RECEIPT-LEDGER-RICE",
            product_type="RAW",
            base_unit="bag",
        )
        cls.po = PurchaseOrder.objects.create(
            supplier=cls.supplier,
            department=cls.department,
            status="APPROVED",
        )
        cls.item = PurchaseItem.objects.create(
            purchase_order=cls.po,
            product=cls.product,
            purchase_quantity=20,
            unit_cost=Decimal("15000.00"),
        )

    def test_receipts_accumulate_against_ordered_quantity(self):
        first = PurchaseReceipt.objects.create(
            purchase_order=self.po,
            notes="First delivery",
        )
        PurchaseReceiptItem.objects.create(
            receipt=first,
            purchase_item=self.item,
            quantity=8,
            unit_cost=Decimal("15000.00"),
        )

        second = PurchaseReceipt.objects.create(
            purchase_order=self.po,
            notes="Balance delivery",
        )
        PurchaseReceiptItem.objects.create(
            receipt=second,
            purchase_item=self.item,
            quantity=12,
            unit_cost=Decimal("15000.00"),
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.purchase_quantity, 20)
        self.assertEqual(self.item.received_quantity, 20)
        self.assertEqual(self.item.outstanding_quantity, 0)
        self.assertTrue(self.item.is_fully_received)

    def test_receipt_line_preserves_delivery_cost_snapshot(self):
        receipt = PurchaseReceipt.objects.create(
            purchase_order=self.po,
        )
        line = PurchaseReceiptItem.objects.create(
            receipt=receipt,
            purchase_item=self.item,
            quantity=3,
            unit_cost=Decimal("14500.00"),
        )

        self.assertEqual(line.total_cost, Decimal("43500.00"))
        self.assertEqual(self.item.received_quantity, 3)
        self.assertEqual(self.item.outstanding_quantity, 17)
        self.assertFalse(self.item.is_fully_received)

    def test_one_po_line_can_only_appear_once_per_receipt(self):
        from django.db import IntegrityError, transaction

        receipt = PurchaseReceipt.objects.create(
            purchase_order=self.po,
        )
        PurchaseReceiptItem.objects.create(
            receipt=receipt,
            purchase_item=self.item,
            quantity=2,
            unit_cost=Decimal("15000.00"),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PurchaseReceiptItem.objects.create(
                    receipt=receipt,
                    purchase_item=self.item,
                    quantity=1,
                    unit_cost=Decimal("15000.00"),
                )



class PartialPurchaseOrderReceivingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Partial PO Receipt Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Partial PO Receipt Hotel",
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="PPR",
            name="Partial PO Receipt Store",
            department_type="STORE",
        )
        cls.supplier = Supplier.objects.create(
            hotel=cls.hotel,
            name="Partial PO Receipt Supplier",
        )
        cls.user = User.objects.create_user(
            username="partial_po_receiver",
            password="test-password",
            role="STORE",
            hotel=cls.hotel,
            department=cls.department,
        )

    def setUp(self):
        self.product = Product.objects.create(
            hotel=self.hotel,
            name="Partial Receipt Cooking Oil",
            sku="PARTIAL-RECEIPT-OIL",
            product_type="RAW",
            base_unit="litre",
            purchase_unit="carton",
            unit_multiplier=12,
            purchase_cost=Decimal("24000.00"),
        )
        self.po = PurchaseOrder.objects.create(
            supplier=self.supplier,
            department=self.department,
            status="APPROVED",
            created_by=self.user,
        )
        self.item = PurchaseItem.objects.create(
            purchase_order=self.po,
            product=self.product,
            purchase_quantity=10,
            unit_cost=Decimal("24000.00"),
        )

    def test_supplier_payable_uses_only_quantities_physically_received(self):
        from accounting.services.supplier_payments import purchase_order_received_total

        receipt = PurchaseReceipt.objects.create(
            purchase_order=self.po,
            received_by=self.user,
        )
        PurchaseReceiptItem.objects.create(
            receipt=receipt,
            purchase_item=self.item,
            quantity=3,
            unit_cost=Decimal("24000.00"),
        )

        self.assertEqual(
            purchase_order_received_total(self.po),
            Decimal("72000.00"),
        )

    def test_supplier_payable_aggregates_receipt_cost_snapshots(self):
        from accounting.services.supplier_payments import purchase_order_received_total

        first = PurchaseReceipt.objects.create(purchase_order=self.po)
        PurchaseReceiptItem.objects.create(
            receipt=first,
            purchase_item=self.item,
            quantity=2,
            unit_cost=Decimal("24000.00"),
        )
        second = PurchaseReceipt.objects.create(purchase_order=self.po)
        PurchaseReceiptItem.objects.create(
            receipt=second,
            purchase_item=self.item,
            quantity=1,
            unit_cost=Decimal("25000.00"),
        )

        self.assertEqual(
            purchase_order_received_total(self.po),
            Decimal("73000.00"),
        )

    def test_supplier_payable_is_zero_before_any_receipt(self):
        from accounting.services.supplier_payments import purchase_order_received_total

        self.assertEqual(
            purchase_order_received_total(self.po),
            Decimal("0.00"),
        )

    @patch("accounting.services.postings.purchase.post_inventory_receipt")
    def test_partial_receipt_updates_stock_fifo_and_remaining_balance(self, post_receipt):
        receipt = self.po.receive(
            self.user,
            {str(self.item.pk): "3"},
            notes="First three cartons",
        )

        self.po.refresh_from_db()
        self.item.refresh_from_db()
        stock = Stock.objects.get(product=self.product, department=self.department)
        layer = StockCostLayer.objects.get(product=self.product, department=self.department)
        movement = StockMovement.objects.get(reference=f"PO-{self.po.pk}-REC-{receipt.pk}")

        self.assertEqual(self.po.status, "PARTIALLY_RECEIVED")
        self.assertEqual(self.item.received_quantity, 3)
        self.assertEqual(self.item.outstanding_quantity, 7)
        self.assertEqual(stock.quantity, Decimal("36.000"))
        self.assertEqual(movement.quantity, Decimal("36.000"))
        self.assertEqual(layer.quantity_received, Decimal("36.000"))
        self.assertEqual(layer.quantity_remaining, Decimal("36.000"))
        self.assertEqual(layer.unit_cost, Decimal("2000.0000"))
        self.assertEqual(layer.source_reference, f"PO-{self.po.pk}-REC-{receipt.pk}")
        post_receipt.assert_called_once_with(receipt)

    @patch("accounting.services.postings.purchase.post_inventory_receipt")
    def test_final_receipt_completes_order_without_reposting_prior_quantities(self, post_receipt):
        first = self.po.receive(self.user, {str(self.item.pk): "3"})
        second = self.po.receive(self.user, {str(self.item.pk): "7"})

        self.po.refresh_from_db()
        self.item.refresh_from_db()
        stock = Stock.objects.get(product=self.product, department=self.department)

        self.assertEqual(self.po.status, "RECEIVED")
        self.assertEqual(self.item.received_quantity, 10)
        self.assertEqual(self.item.outstanding_quantity, 0)
        self.assertEqual(stock.quantity, Decimal("120.000"))
        self.assertEqual(
            StockMovement.objects.filter(
                reference__in=[
                    f"PO-{self.po.pk}-REC-{first.pk}",
                    f"PO-{self.po.pk}-REC-{second.pk}",
                ],
                movement_type="IN",
            ).count(),
            2,
        )
        self.assertEqual(post_receipt.call_count, 2)

    @patch("accounting.services.postings.purchase.post_inventory_receipt")
    def test_receipt_cannot_exceed_outstanding_quantity(self, post_receipt):
        with self.assertRaises(ValidationError):
            self.po.receive(self.user, {str(self.item.pk): "11"})

        self.po.refresh_from_db()
        self.assertEqual(self.po.status, "APPROVED")
        self.assertFalse(PurchaseReceipt.objects.filter(purchase_order=self.po).exists())
        self.assertFalse(Stock.objects.filter(product=self.product, department=self.department).exists())
        post_receipt.assert_not_called()

    @patch("accounting.services.postings.purchase.post_inventory_receipt")
    def test_equipment_receipt_creates_only_delivered_asset_records(self, post_receipt):
        equipment = Product.objects.create(
            hotel=self.hotel,
            name="Asset Laptop",
            sku="PARTIAL-RECEIPT-LAPTOP",
            product_type="EQUIPMENT",
            usage_type="ASSET",
            base_unit="unit",
            purchase_unit="unit",
            unit_multiplier=1,
            purchase_cost=Decimal("500000.00"),
        )
        equipment_item = PurchaseItem.objects.create(
            purchase_order=self.po,
            product=equipment,
            purchase_quantity=4,
            unit_cost=Decimal("500000.00"),
        )

        self.po.receive(
            self.user,
            {
                str(self.item.pk): "0",
                str(equipment_item.pk): "2",
            },
        )

        from maintenance.models import MaintenanceAsset
        assets = MaintenanceAsset.objects.filter(purchase_item=equipment_item)

        self.assertEqual(assets.count(), 2)
        self.assertEqual(
            assets.aggregate(total=models.Sum("purchase_cost"))["total"],
            Decimal("1000000.00"),
        )
