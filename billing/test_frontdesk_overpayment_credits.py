from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import Account, JournalEntry
from billing.models import (
    Charge, ChargePaymentAllocation, Folio, Guest, GuestDeposit,
    GuestDepositTransaction, Payment,
)
from frontdesk.workflows.payment import take_payment_for_folio
from inventory.models import Department, Hotel, Organization


class FrontdeskOverpaymentCreditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Overpayment Credit Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Overpayment Credit Hotel",
        )
        cls.frontdesk = Department.objects.create(
            hotel=cls.hotel,
            code="OVF",
            name="Overpayment Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="overpayment_frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Test",
            last_name="Guest",
        )
        for code, name, account_type, system_key in (
            ("1100", "Accounts Receivable", "current_asset", "accounts_receivable"),
            ("2200", "Guest Deposits", "current_liability", "guest_deposits"),
        ):
            Account.objects.get_or_create(
                hotel=cls.hotel,
                system_key=system_key,
                is_system=True,
                defaults={
                    "code": code,
                    "name": name,
                    "account_type": account_type,
                },
            )

    def setUp(self):
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            guest=self.guest,
            # Room is not required by this service-level test.
        )
        self.charge = Charge.objects.create(
            folio=self.folio,
            description="Existing room charge",
            department=self.frontdesk,
            amount=Decimal("2000.00"),
            reference="TEST-ROOM-CHARGE",
        )

    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_overpayment_allocates_balance_and_records_excess_as_guest_credit(self, post_accounting):
        payment = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("10000.00"),
            method="CASH",
            note="Guest paid more than outstanding",
        )

        self.folio.refresh_from_db()
        deposit = GuestDeposit.objects.get(guest=self.guest, hotel=self.hotel)
        credit = GuestDepositTransaction.objects.get(
            deposit=deposit,
            transaction_type="UNAPPLIED_CREDIT",
        )
        allocation = ChargePaymentAllocation.objects.get(payment=payment)
        journal = JournalEntry.objects.get(
            hotel=self.hotel,
            reference=f"FOLIO-OVERPAYMENT-{payment.pk}",
        )
        lines = {
            line.account.system_key: (line.debit, line.credit)
            for line in journal.lines.select_related("account")
        }

        self.assertEqual(payment.amount, Decimal("10000.00"))
        self.assertTrue(payment.allocation_tracking_enabled)
        self.assertEqual(allocation.charge_id, self.charge.pk)
        self.assertEqual(allocation.amount, Decimal("2000.00"))
        self.assertEqual(self.folio.balance, Decimal("0.00"))
        self.assertEqual(deposit.balance, Decimal("8000.00"))
        self.assertEqual(credit.amount, Decimal("8000.00"))
        self.assertEqual(credit.folio_id, self.folio.pk)
        self.assertEqual(
            lines["accounts_receivable"],
            (Decimal("8000.00"), Decimal("0.00")),
        )
        self.assertEqual(
            lines["guest_deposits"],
            (Decimal("0.00"), Decimal("8000.00")),
        )
        post_accounting.assert_called_once_with(payment)

    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_exact_payment_does_not_create_guest_credit(self, post_accounting):
        payment = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("2000.00"),
            method="CASH",
        )

        self.folio.refresh_from_db()
        self.assertEqual(self.folio.balance, Decimal("0.00"))
        self.assertFalse(GuestDeposit.objects.filter(guest=self.guest).exists())
        self.assertFalse(
            GuestDepositTransaction.objects.filter(
                transaction_type="UNAPPLIED_CREDIT"
            ).exists()
        )
        self.assertEqual(payment.amount, Decimal("2000.00"))
        post_accounting.assert_called_once_with(payment)

    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_partial_payment_only_reduces_folio_by_allocated_amount(self, post_accounting):
        payment = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("750.00"),
            method="CASH",
        )

        self.folio.refresh_from_db()
        self.assertEqual(self.folio.balance, Decimal("1250.00"))
        self.assertEqual(payment.amount, Decimal("750.00"))
        self.assertFalse(GuestDeposit.objects.filter(guest=self.guest).exists())
        post_accounting.assert_called_once_with(payment)
