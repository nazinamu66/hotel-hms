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
from billing.services.guest_deposit import apply_guest_deposit
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
        self.assertEqual(payment.applied_amount, Decimal("2000.00"))
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
        self.assertEqual(payment.applied_amount, Decimal("2000.00"))
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
        self.assertEqual(payment.applied_amount, Decimal("750.00"))
        self.assertFalse(GuestDeposit.objects.filter(guest=self.guest).exists())
        post_accounting.assert_called_once_with(payment)

    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_unapplied_credit_can_later_be_applied_to_another_folio(self, post_accounting):
        original_payment = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("10000.00"),
            method="CASH",
        )
        deposit = GuestDeposit.objects.get(guest=self.guest, hotel=self.hotel)
        next_folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            guest=self.guest,
        )
        next_charge = Charge.objects.create(
            folio=next_folio,
            description="Next stay charge",
            department=self.frontdesk,
            amount=Decimal("3000.00"),
            reference="NEXT-STAY-CHARGE",
        )

        deposit_payment, transaction_record = apply_guest_deposit(
            folio=next_folio,
            amount=Decimal("3000.00"),
            user=self.user,
            reference="APPLY-CREDIT-3000",
            note="Apply existing guest credit",
        )

        self.folio.refresh_from_db()
        next_folio.refresh_from_db()
        deposit.refresh_from_db()

        self.assertEqual(original_payment.amount, Decimal("10000.00"))
        self.assertEqual(self.folio.balance, Decimal("0.00"))
        self.assertEqual(next_folio.balance, Decimal("0.00"))
        self.assertEqual(deposit.balance, Decimal("5000.00"))
        self.assertEqual(deposit_payment.amount, Decimal("3000.00"))
        self.assertTrue(deposit_payment.allocation_tracking_enabled)
        self.assertEqual(transaction_record.transaction_type, "APPLIED")
        self.assertEqual(
            ChargePaymentAllocation.objects.get(payment=deposit_payment).charge_id,
            next_charge.pk,
        )
        post_accounting.assert_called_once_with(original_payment)

    @patch("billing.services.guest_deposit.apply_guest_deposit")
    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_guest_deposit_method_uses_deposit_ledger_not_cash_receipt_posting(
        self, post_accounting, apply_deposit
    ):
        expected_payment = Payment(
            folio=self.folio,
            amount=Decimal("500.00"),
            method="DEPOSIT",
        )
        apply_deposit.return_value = (expected_payment, object())

        result = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("500.00"),
            method="DEPOSIT",
        )

        self.assertIs(result, expected_payment)
        apply_deposit.assert_called_once_with(
            folio=self.folio,
            amount=Decimal("500.00"),
            user=self.user,
            reference="",
            note="",
        )
        post_accounting.assert_not_called()

    @patch("frontdesk.workflows.payment.post_payment_to_accounting")
    def test_overpayment_on_unreconciled_legacy_charge_reduces_folio_without_guessing_charge(
        self, post_accounting
    ):
        self.charge.allocation_enabled = False
        self.charge.save(update_fields=["allocation_enabled"])

        payment = take_payment_for_folio(
            folio=self.folio,
            user=self.user,
            amount=Decimal("10000.00"),
            method="CASH",
        )

        self.folio.refresh_from_db()
        deposit = GuestDeposit.objects.get(guest=self.guest, hotel=self.hotel)

        self.assertEqual(payment.applied_amount, Decimal("2000.00"))
        self.assertEqual(self.folio.balance, Decimal("0.00"))
        self.assertEqual(deposit.balance, Decimal("8000.00"))
        self.assertFalse(ChargePaymentAllocation.objects.filter(payment=payment).exists())
        post_accounting.assert_called_once_with(payment)
