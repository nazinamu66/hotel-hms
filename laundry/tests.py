from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from billing.models import Charge, Folio
from inventory.models import Department, Hotel
from laundry.models import GuestLaundryOrder
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
