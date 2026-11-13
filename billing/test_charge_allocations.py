from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.models import Charge, ChargePaymentAllocation, Folio, Payment
from billing.services.charge_allocations import (
    allocate_payment_fifo,
    allocate_payment_to_charge,
    charge_outstanding,
    get_charge_allocation_status,
    payment_unallocated,
)
from inventory.models import Department, Hotel, Organization


class ChargePaymentAllocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Allocation Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Allocation Test Hotel",
        )
        cls.frontdesk = Department.objects.create(
            hotel=cls.hotel,
            code="ALF",
            name="Allocation Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="allocation_test_user",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )

    def setUp(self):
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="WALKIN",
        )

    def make_charge(self, amount, reference):
        return Charge.objects.create(
            folio=self.folio,
            department=self.frontdesk,
            description=reference,
            amount=Decimal(amount),
            reference=reference,
        )

    def make_payment(self, amount, reference="GENERAL-RECEIPT", method="TRANSFER"):
        return Payment.objects.create(
            folio=self.folio,
            amount=Decimal(amount),
            method=method,
            reference=reference,
            collected_by=self.user,
        )

    def test_general_payment_allocates_fifo_across_oldest_charges(self):
        first = self.make_charge("3000.00", "ROOM-001-2026-11-01")
        second = self.make_charge("4000.00", "LAUNDRY-17")
        payment = self.make_payment("5000.00")

        allocations = allocate_payment_fifo(payment)

        self.assertEqual(len(allocations), 2)
        self.assertEqual(
            list(ChargePaymentAllocation.objects.filter(payment=payment).values_list(
                "charge_id", "amount"
            )),
            [(first.pk, Decimal("3000.00")), (second.pk, Decimal("2000.00"))],
        )
        self.assertEqual(charge_outstanding(first), Decimal("0.00"))
        self.assertEqual(charge_outstanding(second), Decimal("2000.00"))
        self.assertEqual(payment_unallocated(payment), Decimal("0.00"))

    def test_partial_payment_leaves_charge_balance_outstanding(self):
        charge = self.make_charge("7000.00", "ROOM-001-2026-11-02")
        payment = self.make_payment("2500.00")

        allocate_payment_fifo(payment)

        self.assertEqual(charge_outstanding(charge), Decimal("4500.00"))
        self.assertEqual(payment_unallocated(payment), Decimal("0.00"))

    def test_payment_excess_remains_unallocated(self):
        self.make_charge("2000.00", "ROOM-001-2026-11-03")
        payment = self.make_payment("3000.00")

        allocate_payment_fifo(payment)

        self.assertEqual(payment_unallocated(payment), Decimal("1000.00"))

    def test_pos_payment_allocates_only_to_matching_charge(self):
        first = self.make_charge("3000.00", "ROOM-001-2026-11-04")
        pos_charge = self.make_charge("2500.00", "POS-42")
        payment = self.make_payment("2500.00", reference="POS-42", method="CASH")

        allocation = allocate_payment_to_charge(payment, pos_charge)

        self.assertEqual(allocation.charge_id, pos_charge.pk)
        self.assertEqual(charge_outstanding(first), Decimal("3000.00"))
        self.assertEqual(charge_outstanding(pos_charge), Decimal("0.00"))

    def test_pos_payment_cannot_be_allocated_to_another_folio_charge(self):
        self.make_charge("3000.00", "ROOM-001-2026-11-05")
        payment = self.make_payment("2500.00", reference="POS-43", method="CASH")
        other_charge = Charge.objects.create(
            folio=Folio.objects.create(hotel=self.hotel, folio_type="WALKIN"),
            department=self.frontdesk,
            description="Other folio charge",
            amount=Decimal("2500.00"),
            reference="POS-OTHER",
        )

        with self.assertRaisesMessage(ValidationError, "same folio"):
            allocate_payment_to_charge(payment, other_charge)

    def test_historical_charges_and_payments_are_not_guessed_or_backfilled(self):
        charge = self.make_charge("1000.00", "LEGACY-CHARGE")
        charge.allocation_enabled = False
        charge.save(update_fields=["allocation_enabled"])
        payment = self.make_payment("1000.00", reference="LEGACY-PAYMENT")

        allocate_payment_fifo(payment)

        status = get_charge_allocation_status(charge)
        self.assertFalse(status["reconciled"])
        self.assertIsNone(status["outstanding"])
        self.assertEqual(payment_unallocated(payment), Decimal("1000.00"))
        self.assertFalse(ChargePaymentAllocation.objects.filter(payment=payment).exists())

    def test_general_frontdesk_pos_terminal_payment_still_uses_fifo(self):
        charge = self.make_charge("1500.00", "ROOM-001-2026-11-06")
        payment = self.make_payment(
            "1500.00",
            reference="POS-TERMINAL-REF-9",
            method="POS",
        )

        allocate_payment_fifo(payment)

        self.assertEqual(charge_outstanding(charge), Decimal("0.00"))
