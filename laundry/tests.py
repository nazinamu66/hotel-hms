from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from billing.models import Charge, Folio
from inventory.models import Department, Hotel
from laundry.models import GuestLaundryOrder, LaundryReceipt
from laundry.services.receiving import confirm_laundry_receipt, create_laundry_receipt
from linen.models import LinenCustody, LinenItem, LinenTransaction
from linen.services.balances import get_linen_condition_balance, get_linen_summary
from linen.services.workflows import return_linen_to_laundry
from inventory.models import Product
from laundry.services.guest_laundry import (
    cancel_guest_laundry_order,
    create_guest_laundry_order,
    deliver_guest_laundry_order,
    transition_guest_laundry_order,
)


class GuestLaundryServiceTests(TestCase):
    def setUp(self):
        self.hotel = Hotel.objects.create(
            name="Guest Laundry Test Hotel",
        )
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Laundry",
            defaults={
                "code": "LAUN",
                "department_type": "LAUNDRY",
                "is_active": True,
            },
        )
        self.assertEqual(self.department.department_type, "LAUNDRY")
        self.user = User.objects.create_user(
            username="laundry-test-user",
            password="test-password",
            role="LAUNDRY",
            hotel=self.hotel,
            department=self.department,
        )
        self.folio = Folio.objects.create(
            folio_type="WALKIN",
            hotel=self.hotel,
        )

    def create_order(self, **overrides):
        values = {
            "folio": self.folio,
            "description": "Bedsheet washing",
            "quantity": 2,
            "unit_price": Decimal("2000.00"),
            "user": self.user,
            "customer_name": "Walk-in Customer",
            "customer_phone": "08000000000",
        }
        values.update(overrides)
        return create_guest_laundry_order(**values)

    def make_ready_order(self):
        order, item = self.create_order()
        transition_guest_laundry_order(order.id, "RECEIVED")
        transition_guest_laundry_order(order.id, "PROCESSING")
        return transition_guest_laundry_order(order.id, "READY")

    def test_create_walkin_order_saves_item_snapshot_and_total(self):
        order, item = self.create_order(
            description="  Bedsheet washing  ",
            quantity="3",
            unit_price="1500.00",
            customer_name="  Musa  ",
        )

        self.assertEqual(order.status, "NEW")
        self.assertEqual(order.customer_name, "Musa")
        self.assertEqual(item.description, "Bedsheet washing")
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.unit_price, Decimal("1500.00"))
        self.assertEqual(item.amount, Decimal("4500.00"))
        self.assertEqual(order.total_amount, Decimal("4500.00"))
        self.assertEqual(order.created_by, self.user)

    def test_create_order_rejects_invalid_quantity_and_missing_walkin_name(self):
        with self.assertRaises(ValidationError):
            self.create_order(quantity="0")

        with self.assertRaises(ValidationError):
            self.create_order(customer_name=" ")

        self.assertEqual(GuestLaundryOrder.objects.count(), 0)

    def test_status_transitions_record_their_timestamps(self):
        order, _ = self.create_order()

        received = transition_guest_laundry_order(order.id, "RECEIVED")
        self.assertEqual(received.status, "RECEIVED")
        self.assertIsNotNone(received.received_at)

        processing = transition_guest_laundry_order(order.id, "PROCESSING")
        self.assertEqual(processing.status, "PROCESSING")
        self.assertIsNotNone(processing.processing_at)

        ready = transition_guest_laundry_order(order.id, "READY")
        self.assertEqual(ready.status, "READY")
        self.assertIsNotNone(ready.ready_at)

    def test_invalid_status_transition_is_rejected(self):
        order, _ = self.create_order()

        with self.assertRaises(ValidationError):
            transition_guest_laundry_order(order.id, "READY")

        order.refresh_from_db()
        self.assertEqual(order.status, "NEW")

    def test_order_can_be_cancelled_before_processing(self):
        order, _ = self.create_order()

        cancelled = cancel_guest_laundry_order(order.id)

        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertIsNotNone(cancelled.cancelled_at)

    def test_order_cannot_be_cancelled_after_processing_starts(self):
        order, _ = self.create_order()
        transition_guest_laundry_order(order.id, "RECEIVED")
        transition_guest_laundry_order(order.id, "PROCESSING")

        with self.assertRaises(ValidationError):
            cancel_guest_laundry_order(order.id)

        order.refresh_from_db()
        self.assertEqual(order.status, "PROCESSING")

    @patch("laundry.services.guest_laundry.post_guest_laundry_charge")
    def test_delivery_creates_one_folio_charge_and_posts_accounting(self, post_charge):
        order = self.make_ready_order()

        delivered, charge = deliver_guest_laundry_order(order.id)

        self.assertEqual(delivered.status, "DELIVERED")
        self.assertIsNotNone(delivered.delivered_at)
        self.assertEqual(delivered.billing_charge_id, charge.id)
        self.assertEqual(charge.folio, self.folio)
        self.assertEqual(charge.department, self.department)
        self.assertEqual(charge.amount, Decimal("4000.00"))
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 1)
        post_charge.assert_called_once_with(delivered, charge)

    @patch("laundry.services.guest_laundry.post_guest_laundry_charge")
    def test_repeated_delivery_does_not_duplicate_charge_or_journal(self, post_charge):
        order = self.make_ready_order()

        first_order, first_charge = deliver_guest_laundry_order(order.id)
        second_order, second_charge = deliver_guest_laundry_order(order.id)

        self.assertEqual(first_charge.id, second_charge.id)
        self.assertEqual(first_order.billing_charge_id, second_order.billing_charge_id)
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 1)
        self.assertEqual(post_charge.call_count, 2)

    @patch(
        "laundry.services.guest_laundry.post_guest_laundry_charge",
        side_effect=ValidationError("Accounting posting failed."),
    )
    def test_accounting_failure_rolls_back_charge_and_delivery(self, post_charge):
        order = self.make_ready_order()

        with self.assertRaises(ValidationError):
            deliver_guest_laundry_order(order.id)

        order.refresh_from_db()
        self.assertEqual(order.status, "READY")
        self.assertIsNone(order.billing_charge_id)
        self.assertIsNone(order.delivered_at)
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 0)
        post_charge.assert_called_once()

    @patch("laundry.services.guest_laundry.post_guest_laundry_charge")
    def test_delivery_rejects_order_that_is_not_ready(self, post_charge):
        order, _ = self.create_order()

        with self.assertRaises(ValidationError):
            deliver_guest_laundry_order(order.id)

        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 0)
        post_charge.assert_not_called()


class LaundryReceiptReconciliationTests(TestCase):
    def setUp(self):
        from linen.models import LinenItem
        self.hotel = Hotel.objects.create(name="Receipt Reconciliation Hotel")
        self.department = Department.objects.filter(
            hotel=self.hotel,
            department_type="LAUNDRY",
            is_active=True,
        ).first()
        if self.department is None:
            self.department = Department.objects.create(
                hotel=self.hotel,
                code="LAUN",
                name="Laundry",
                department_type="LAUNDRY",
                is_active=True,
            )
        self.user = User.objects.create_user(
            username="receipt-manager",
            password="test-password",
            role="MANAGER",
            hotel=self.hotel,
            department=self.department,
        )
        self.product = __import__("inventory.models", fromlist=["Product"]).Product.objects.create(
            hotel=self.hotel,
            name="Receipt Test Bedsheet",
            sku="RECEIPT-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        self.linen_item = LinenItem.objects.create(product=self.product)
        from linen.models import LinenTransaction
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=10,
            condition="CLEAN",
            event_type="RECEIVED",
            to_location="LAUNDRY",
            performed_by=self.user,
            reference="TEST-OPENING",
        )
        from laundry.models import LaundryReceipt
        self.receipt = LaundryReceipt.objects.create(
            linen_item=self.linen_item,
            returned_by=self.user,
            declared_clean_quantity=10,
            declared_dirty_quantity=0,
            reference="TEST-RETURN-1",
        )

    def test_short_receipt_moves_difference_to_holding_and_marks_pending(self):
        from laundry.models import LaundryReceiptDiscrepancy
        from laundry.services.receiving import confirm_laundry_receipt
        from linen.services.balances import get_linen_condition_balance

        receipt = confirm_laundry_receipt(
            receipt=self.receipt,
            received_by=self.user,
            received_clean_quantity=8,
            received_dirty_quantity=0,
        )

        self.assertEqual(receipt.status, "DISCREPANCY_PENDING")
        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "LAUNDRY")["CLEAN"],
            8,
        )
        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "DISCREPANCY")["CLEAN"],
            2,
        )
        discrepancy = LaundryReceiptDiscrepancy.objects.get(
            receipt=receipt,
            condition="CLEAN",
        )
        self.assertEqual(discrepancy.quantity, 2)
        self.assertEqual(discrepancy.status, "OPEN")

    def test_recovered_discrepancy_returns_quantity_to_laundry_once(self):
        from laundry.services.receiving import (
            confirm_laundry_receipt,
            resolve_laundry_receipt_discrepancy,
        )
        from linen.services.balances import get_linen_condition_balance

        receipt = confirm_laundry_receipt(
            receipt=self.receipt,
            received_by=self.user,
            received_clean_quantity=8,
            received_dirty_quantity=0,
        )
        discrepancy = receipt.discrepancies.get(condition="CLEAN")

        resolve_laundry_receipt_discrepancy(
            discrepancy=discrepancy,
            resolution="RECOVERED",
            resolved_by=self.user,
            note="Two sheets were located.",
        )

        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "LAUNDRY")["CLEAN"],
            10,
        )
        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "DISCREPANCY")["CLEAN"],
            0,
        )
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "DISCREPANCY_RESOLVED")

        with self.assertRaises(ValidationError):
            resolve_laundry_receipt_discrepancy(
                discrepancy=discrepancy,
                resolution="RECOVERED",
                resolved_by=self.user,
            )

    def test_confirmed_loss_removes_quantity_from_holding_not_laundry(self):
        from laundry.services.receiving import (
            confirm_laundry_receipt,
            resolve_laundry_receipt_discrepancy,
        )
        from linen.services.balances import get_linen_condition_balance

        receipt = confirm_laundry_receipt(
            receipt=self.receipt,
            received_by=self.user,
            received_clean_quantity=8,
            received_dirty_quantity=0,
        )
        discrepancy = receipt.discrepancies.get(condition="CLEAN")

        resolve_laundry_receipt_discrepancy(
            discrepancy=discrepancy,
            resolution="LOST",
            resolved_by=self.user,
            note="Investigation confirmed loss.",
        )

        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "LAUNDRY")["CLEAN"],
            8,
        )
        self.assertEqual(
            get_linen_condition_balance(self.linen_item, "DISCREPANCY")["CLEAN"],
            0,
        )

class HousekeepingLinenTransitReceiptTests(TestCase):
    def setUp(self):
        self.hotel = Hotel.objects.create(name="Linen Transit Test Hotel")
        self.hk_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="HOUSEKEEPING",
            defaults={
                "code": "HK",
                "name": "Housekeeping",
                "is_active": True,
            },
        )
        self.laundry_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="LAUNDRY",
            defaults={
                "code": "LAUN",
                "name": "Laundry",
                "is_active": True,
            },
        )
        self.housekeeper = User.objects.create_user(
            username="transit-housekeeper",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=self.hotel,
            department=self.hk_department,
        )
        self.laundry_user = User.objects.create_user(
            username="transit-laundry-user",
            password="test-password",
            role="LAUNDRY",
            hotel=self.hotel,
            department=self.laundry_department,
        )
        product = Product.objects.create(
            hotel=self.hotel,
            name="Transit Bedsheet",
            sku="TRANSIT-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        self.linen_item = LinenItem.objects.create(product=product)
        self.custody = LinenCustody.objects.create(
            linen_item=self.linen_item,
            user=self.housekeeper,
            clean_quantity=3,
            dirty_quantity=4,
        )
        # Establish the department-level Housekeeping balance that
        # corresponds to the employee's current physical custody.
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=7,
            condition="CLEAN",
            event_type="RECEIVED",
            to_location="HOUSEKEEPING",
            performed_by=self.housekeeper,
            reference="TRANSIT-OPENING",
        )
        # Correct the dirty condition without changing total location stock.
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=4,
            condition="DIRTY",
            event_type="ADJUSTMENT",
            to_location="HOUSEKEEPING",
            performed_by=self.housekeeper,
            reference="TRANSIT-OPENING-DIRTY",
        )

    def test_handover_is_in_transit_until_laundry_confirms_it(self):
        receipt = return_linen_to_laundry(
            linen_item=self.linen_item,
            user=self.housekeeper,
            clean_quantity=2,
            dirty_quantity=3,
            note="End of shift return",
        )

        self.custody.refresh_from_db()
        self.assertEqual(self.custody.clean_quantity, 1)
        self.assertEqual(self.custody.dirty_quantity, 1)
        self.assertEqual(receipt.status, "PENDING")

        summary = get_linen_summary(self.linen_item)
        self.assertEqual(summary["in_transit"], 5)
        laundry = get_linen_condition_balance(self.linen_item, "LAUNDRY")
        self.assertEqual(laundry["CLEAN"], 0)
        self.assertEqual(laundry["DIRTY"], 0)

        confirm_laundry_receipt(
            receipt=receipt,
            received_by=self.laundry_user,
            received_clean_quantity=2,
            received_dirty_quantity=3,
        )

        summary = get_linen_summary(self.linen_item)
        self.assertEqual(summary["in_transit"], 0)
        laundry = get_linen_condition_balance(self.linen_item, "LAUNDRY")
        self.assertEqual(laundry["CLEAN"], 2)
        self.assertEqual(laundry["DIRTY"], 3)

    def test_short_return_moves_only_confirmed_quantity_to_laundry(self):
        receipt = return_linen_to_laundry(
            linen_item=self.linen_item,
            user=self.housekeeper,
            clean_quantity=2,
            dirty_quantity=3,
            note="Short count test",
        )

        confirm_laundry_receipt(
            receipt=receipt,
            received_by=self.laundry_user,
            received_clean_quantity=1,
            received_dirty_quantity=2,
        )

        summary = get_linen_summary(self.linen_item)
        self.assertEqual(summary["in_transit"], 0)
        self.assertEqual(summary["discrepancy"], 2)
        laundry = get_linen_condition_balance(self.linen_item, "LAUNDRY")
        self.assertEqual(laundry["CLEAN"], 1)
        self.assertEqual(laundry["DIRTY"], 2)
        self.assertEqual(receipt.discrepancies.count(), 2)

    def test_legacy_receipt_confirmation_keeps_original_location_flow(self):
        reference = "LEGACY-LAUNDRY-RETURN"
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=2,
            condition="CLEAN",
            event_type="SENT_TO_LAUNDRY",
            from_location="HOUSEKEEPING",
            to_location="LAUNDRY",
            performed_by=self.housekeeper,
            from_user=self.housekeeper,
            reference=reference,
        )
        receipt = create_laundry_receipt(
            linen_item=self.linen_item,
            returned_by=self.housekeeper,
            declared_clean_quantity=2,
            reference=reference,
        )

        confirm_laundry_receipt(
            receipt=receipt,
            received_by=self.laundry_user,
            received_clean_quantity=1,
        )

        laundry = get_linen_condition_balance(self.linen_item, "LAUNDRY")
        self.assertEqual(laundry["CLEAN"], 1)
        summary = get_linen_summary(self.linen_item)
        self.assertEqual(summary["in_transit"], 0)
        self.assertEqual(summary["discrepancy"], 1)

