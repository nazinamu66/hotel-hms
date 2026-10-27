from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import Account, JournalEntry, JournalLine, SupplierLedger, SupplierPayment
from accounting.services.direct_purchase_accounting import (
    direct_purchase_received_value,
    get_supplier_advances_account,
    post_direct_purchase_payment,
    post_direct_purchase_receipt,
)
from inventory.models import Hotel, Organization, Product, Supplier
from kitchen.models import DirectPurchase, DirectPurchaseItem


User = get_user_model()


class DirectPurchaseAccountingOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Direct Purchase Accounting Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Direct Purchase Accounting Hotel",
        )
        cls.manager = User.objects.create_user(
            username="dp_accounting_manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.supplier = Supplier.objects.create(
            hotel=cls.hotel,
            name="Direct Purchase Supplier",
            is_active=True,
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Direct Purchase Rice",
            sku="DP-ACCOUNTING-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("1000.00"),
        )
        cls.cash = Account.objects.get(hotel=cls.hotel, system_key="cash")
        cls.inventory = Account.objects.get(hotel=cls.hotel, system_key="inventory_asset")
        cls.payable = Account.objects.get(hotel=cls.hotel, system_key="accounts_payable")

    def make_purchase(self):
        purchase = DirectPurchase.objects.create(
            supplier=self.supplier,
            requested_by=self.manager,
            total_cost=Decimal("10000.00"),
            status="APPROVED",
            payment_source="HOTEL_FUNDS",
        )
        item = DirectPurchaseItem.objects.create(
            purchase=purchase,
            product=self.product,
            quantity=Decimal("10.000"),
            unit_cost=Decimal("1000.00"),
        )
        return purchase, item

    def make_payment(self, purchase, amount, reference):
        return SupplierPayment.objects.create(
            hotel=self.hotel,
            supplier=self.supplier,
            source_type="DIRECT",
            source_id=purchase.pk,
            amount=Decimal(amount),
            payment_account=self.cash,
            payment_date="2026-10-28",
            reference=reference,
            created_by=self.manager,
        )

    @staticmethod
    def journal_balances(journal):
        totals = {}
        for line in journal.lines.select_related("account"):
            totals[line.account.system_key or line.account.name] = (
                line.debit,
                line.credit,
            )
        return totals

    def test_goods_received_before_payment_recognizes_inventory_and_payable_only_when_received(self):
        purchase, item = self.make_purchase()

        # Receiving 6 kg first recognizes only the received value.
        advance_used = post_direct_purchase_receipt(
            purchase=purchase,
            item=item,
            received_now=Decimal("6.000"),
            advance_available=Decimal("0.00"),
            created_by=self.manager,
        )
        receipt = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"DP-{purchase.pk}-ITEM-{item.pk}-RCV-6.000",
            entry_type="PURCHASE",
        )
        self.assertEqual(advance_used, Decimal("0.00"))
        self.assertEqual(
            self.journal_balances(receipt),
            {
                "inventory_asset": (Decimal("6000.00"), Decimal("0.00")),
                "accounts_payable": (Decimal("0.00"), Decimal("6000.00")),
            },
        )
        self.assertEqual(
            SupplierLedger.objects.get(journal_entry=receipt).amount,
            Decimal("6000.00"),
        )

        item.quantity_received = Decimal("6.000")
        item.save(update_fields=["quantity_received"])
        payment = self.make_payment(purchase, "2000.00", "DP-GOODS-FIRST-1")
        payment_journal = post_direct_purchase_payment(
            purchase=purchase,
            payment=payment,
            amount=Decimal("2000.00"),
            paid_before=Decimal("0.00"),
            created_by=self.manager,
        )
        payment.journal_entry = payment_journal
        payment.save(update_fields=["journal_entry"])

        self.assertEqual(
            self.journal_balances(payment_journal),
            {
                "accounts_payable": (Decimal("2000.00"), Decimal("0.00")),
                "cash": (Decimal("0.00"), Decimal("2000.00")),
            },
        )
        self.assertEqual(
            SupplierLedger.objects.get(journal_entry=payment_journal).amount,
            Decimal("2000.00"),
        )
        self.assertEqual(direct_purchase_received_value(purchase), Decimal("6000.00"))

    def test_payment_before_goods_is_supplier_advance_then_cleared_by_partial_receipts(self):
        purchase, item = self.make_purchase()
        payment = self.make_payment(purchase, "10000.00", "DP-PAYMENT-FIRST-1")
        payment_journal = post_direct_purchase_payment(
            purchase=purchase,
            payment=payment,
            amount=Decimal("10000.00"),
            paid_before=Decimal("0.00"),
            created_by=self.manager,
        )
        payment.journal_entry = payment_journal
        payment.save(update_fields=["journal_entry"])

        advances = get_supplier_advances_account(self.hotel)
        self.assertEqual(
            self.journal_balances(payment_journal),
            {
                "supplier_advances": (Decimal("10000.00"), Decimal("0.00")),
                "cash": (Decimal("0.00"), Decimal("10000.00")),
            },
        )
        self.assertFalse(SupplierLedger.objects.filter(journal_entry=payment_journal).exists())

        advance_used = post_direct_purchase_receipt(
            purchase=purchase,
            item=item,
            received_now=Decimal("6.000"),
            prior_received_value=Decimal("0.00"),
            advance_available=Decimal("10000.00"),
            created_by=self.manager,
        )
        first_receipt = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"DP-{purchase.pk}-ITEM-{item.pk}-RCV-6.000",
            entry_type="PURCHASE",
        )
        self.assertEqual(advance_used, Decimal("6000.00"))
        self.assertEqual(
            self.journal_balances(first_receipt),
            {
                "inventory_asset": (Decimal("6000.00"), Decimal("0.00")),
                "supplier_advances": (Decimal("0.00"), Decimal("6000.00")),
            },
        )

        item.quantity_received = Decimal("6.000")
        item.save(update_fields=["quantity_received"])
        second_advance_used = post_direct_purchase_receipt(
            purchase=purchase,
            item=item,
            received_now=Decimal("4.000"),
            advance_available=Decimal("4000.00"),
            created_by=self.manager,
        )
        second_receipt = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"DP-{purchase.pk}-ITEM-{item.pk}-RCV-10.000",
            entry_type="PURCHASE",
        )
        self.assertEqual(second_advance_used, Decimal("4000.00"))
        self.assertEqual(
            self.journal_balances(second_receipt),
            {
                "inventory_asset": (Decimal("4000.00"), Decimal("0.00")),
                "supplier_advances": (Decimal("0.00"), Decimal("4000.00")),
            },
        )
        self.assertEqual(advances.get_balance(), Decimal("0.00"))
        self.assertEqual(self.inventory.get_balance(), Decimal("10000.00"))
        self.assertEqual(self.payable.get_balance(), Decimal("0.00"))
