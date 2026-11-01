from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import JournalLine
from inventory.models import Department, Hotel, Organization, Product, Stock, StockCostLayer, StockMovement
from opening_stock.forms import OpeningStockLineForm
from opening_stock.models import OpeningStockBatch, OpeningStockLine
from opening_stock.services import post_opening_stock_batch
from linen.models import LinenItem, LinenTransaction
from linen.services.balances import get_linen_summary
from maintenance.models import MaintenanceAsset


User = get_user_model()


class OpeningStockPostingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Opening Stock Test Organization")
        cls.hotel = Hotel.objects.create(organization=organization, name="Opening Stock Test Hotel")
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="OPN",
            name="Opening Test Store",
            department_type="STORE",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Opening Test Rice",
            sku="OPENING-RICE",
            product_type="RAW",
            base_unit="kg",
            usage_type="CONSUMABLE",
        )
        cls.user = User.objects.create_user(
            username="opening-stock-admin",
            password="test-password",
            role="ADMIN",
        )

    def make_batch(self, reference="OPENING-TEST-001"):
        return OpeningStockBatch.objects.create(
            hotel=self.hotel,
            reference=reference,
            opening_date=date.today(),
            created_by=self.user,
        )

    def test_stock_form_accepts_blank_irrelevant_numeric_fields(self):
        batch = self.make_batch("OPENING-FORM-STOCK")
        form = OpeningStockLineForm(
            data={
                "line_type": "STOCK",
                "product": str(self.product.pk),
                "department": str(self.department.pk),
                "quantity": "5",
                "unit_cost": "2000",
            },
            hotel=self.hotel,
            instance=OpeningStockLine(batch=batch),
        )

        self.assertTrue(form.is_valid(), form.errors)
        line = form.save(commit=False)
        self.assertEqual(line.quantity, Decimal("5"))
        self.assertEqual(line.unit_cost, Decimal("2000"))
        self.assertEqual(line.asset_value, Decimal("0"))

    def test_stock_opening_creates_stock_fifo_movement_and_balanced_journal(self):
        batch = self.make_batch()
        line = OpeningStockLine.objects.create(
            batch=batch,
            line_type="STOCK",
            product=self.product,
            department=self.department,
            quantity=Decimal("12.500"),
            unit_cost=Decimal("80.0000"),
        )

        result = post_opening_stock_batch(batch=batch, user=self.user)

        stock = Stock.objects.get(product=self.product, department=self.department)
        layer = StockCostLayer.objects.get(source_reference=f"OPENING-{batch.pk}-LINE-{line.pk}")
        movement = StockMovement.objects.get(reference=f"OPENING-{batch.pk}-LINE-{line.pk}")
        journal_lines = list(JournalLine.objects.filter(journal=result["journal_entry"]))

        self.assertEqual(stock.quantity, Decimal("12.500"))
        self.assertEqual(layer.quantity_remaining, Decimal("12.500"))
        self.assertEqual(layer.unit_cost, Decimal("80.0000"))
        self.assertTrue(layer.is_opening_balance)
        self.assertEqual(movement.quantity, Decimal("12.500"))
        self.assertEqual(sum((row.debit for row in journal_lines), Decimal("0.00")), Decimal("1000.00"))
        self.assertEqual(sum((row.credit for row in journal_lines), Decimal("0.00")), Decimal("1000.00"))
        batch.refresh_from_db()
        self.assertEqual(batch.status, "POSTED")
        self.assertEqual(batch.journal_entry_id, result["journal_entry"].pk)

    def test_posted_batch_cannot_be_posted_twice(self):
        batch = self.make_batch("OPENING-TEST-002")
        OpeningStockLine.objects.create(
            batch=batch,
            line_type="STOCK",
            product=self.product,
            department=self.department,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("10.0000"),
        )
        post_opening_stock_batch(batch=batch, user=self.user)

        with self.assertRaises(ValidationError):
            post_opening_stock_batch(batch=batch, user=self.user)

        self.assertEqual(
            Stock.objects.get(product=self.product, department=self.department).quantity,
            Decimal("2.000"),
        )
        self.assertEqual(
            StockMovement.objects.filter(reference__startswith=f"OPENING-{batch.pk}-").count(),
            1,
        )

    def test_stock_line_rejects_foreign_hotel_product(self):
        other_org = Organization.objects.create(name="Other Opening Org")
        other_hotel = Hotel.objects.create(organization=other_org, name="Other Opening Hotel")
        foreign_product = Product.objects.create(
            hotel=other_hotel,
            name="Foreign Rice",
            sku="FOREIGN-RICE",
            product_type="RAW",
            base_unit="kg",
        )
        batch = self.make_batch("OPENING-TEST-003")
        line = OpeningStockLine(
            batch=batch,
            line_type="STOCK",
            product=foreign_product,
            department=self.department,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("5.0000"),
        )
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_linen_opening_uses_existing_linen_transaction_ledger(self):
        linen_product = Product.objects.create(
            hotel=self.hotel,
            name="Opening Bedsheet",
            sku="OPENING-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        linen_item = LinenItem.objects.create(product=linen_product, par_level=30, minimum_level=5)
        batch = self.make_batch("OPENING-TEST-LINEN")
        OpeningStockLine.objects.create(
            batch=batch,
            line_type="LINEN",
            linen_item=linen_item,
            linen_location="STORE",
            linen_condition="CLEAN",
            quantity=Decimal("20.000"),
            unit_cost=Decimal("25.0000"),
        )

        result = post_opening_stock_batch(batch=batch, user=self.user)

        transaction = LinenTransaction.objects.get(reference__startswith=f"OPENING-{batch.pk}-")
        self.assertEqual(transaction.event_type, "RECEIVED")
        self.assertEqual(transaction.to_location, "STORE")
        self.assertEqual(transaction.quantity, 20)
        self.assertEqual(get_linen_summary(linen_item)["store"], Decimal("20"))
        self.assertEqual(
            sum((row.credit for row in result["journal_entry"].lines.all()), Decimal("0.00")),
            Decimal("500.00"),
        )

    def test_equipment_opening_registers_individual_asset(self):
        equipment_product = Product.objects.create(
            hotel=self.hotel,
            name="Opening Generator",
            sku="OPENING-GENERATOR",
            product_type="EQUIPMENT",
            usage_type="ASSET",
            base_unit="pcs",
            unit_multiplier=1,
            purchase_cost=Decimal("500.00"),
        )
        batch = self.make_batch("OPENING-TEST-EQUIPMENT")
        OpeningStockLine.objects.create(
            batch=batch,
            line_type="EQUIPMENT",
            asset_name="Backup Generator",
            asset_product=equipment_product,
            asset_condition="GOOD",
            asset_value=Decimal("500.00"),
        )

        result = post_opening_stock_batch(batch=batch, user=self.user)

        asset = MaintenanceAsset.objects.get(hotel=self.hotel, product=equipment_product)
        self.assertEqual(asset.name, "Backup Generator")
        self.assertEqual(asset.condition, "GOOD")
        self.assertEqual(asset.purchase_cost, Decimal("500.00"))
        self.assertEqual(
            sum((row.debit for row in result["journal_entry"].lines.all()), Decimal("0.00")),
            Decimal("500.00"),
        )
