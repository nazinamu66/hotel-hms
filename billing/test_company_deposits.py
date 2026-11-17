from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import Account, BankAccount, JournalEntry
from billing.models import Company, CompanyDeposit, CompanyDepositTransaction
from billing.services.company_deposit import (
    receive_company_deposit,
    refund_company_deposit,
)
from inventory.models import Hotel, Organization


class CompanyDepositServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Corporate Deposit Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Corporate Deposit Test Hotel",
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel,
            name="Deposit Test Company",
        )
        cls.user = None
        cls.cash = Account.objects.get(hotel=cls.hotel, system_key="cash")
        cls.liability = Account.objects.get(
            hotel=cls.hotel, system_key="corporate_deposits"
        )
        bank_account = Account.objects.create(
            hotel=cls.hotel,
            code="9890",
            name="Corporate Deposit Test Bank",
            account_type="bank",
        )
        cls.bank = BankAccount.objects.create(
            hotel=cls.hotel,
            account=bank_account,
            bank_name="Test Bank",
            account_name="Hotel Test Account",
            account_number="0000000000",
        )

    def test_receive_cash_creates_ledger_and_posts_liability(self):
        deposit, entry = receive_company_deposit(
            company=self.company,
            hotel=self.hotel,
            amount=Decimal("50000.00"),
            method="CASH",
            user=self.user,
            note="Advance for group booking",
        )
        self.assertEqual(deposit.balance, Decimal("50000.00"))
        self.assertEqual(entry.transaction_type, "RECEIVED")
        journal = JournalEntry.objects.get(reference=f"CORP-DEP-TXN-{entry.pk}")
        lines = list(journal.lines.order_by("id"))
        self.assertEqual(lines[0].account, self.cash)
        self.assertEqual(lines[0].debit, Decimal("50000.00"))
        self.assertEqual(lines[1].account, self.liability)
        self.assertEqual(lines[1].credit, Decimal("50000.00"))

    def test_receive_transfer_requires_reference_and_hotel_bank(self):
        with self.assertRaises(ValidationError):
            receive_company_deposit(
                company=self.company, hotel=self.hotel,
                amount="10000", method="TRANSFER", user=self.user,
                bank_account=self.bank,
            )
        deposit, record = receive_company_deposit(
            company=self.company, hotel=self.hotel,
            amount="10000", method="TRANSFER", user=self.user,
            reference="BANK-REF-001", bank_account=self.bank,
        )
        self.assertEqual(deposit.balance, Decimal("10000.00"))
        self.assertEqual(record.bank_account, self.bank)

    def test_refund_reduces_balance_and_posts_cash_out(self):
        deposit, _ = receive_company_deposit(
            company=self.company, hotel=self.hotel,
            amount="30000", method="CASH", user=self.user,
        )
        deposit, refund = refund_company_deposit(
            company=self.company, hotel=self.hotel,
            amount="12500", method="CASH", user=self.user,
            note="Unused advance returned",
        )
        self.assertEqual(deposit.balance, Decimal("17500.00"))
        self.assertEqual(refund.transaction_type, "REFUNDED")
        journal = JournalEntry.objects.get(reference=f"CORP-DEP-TXN-{refund.pk}")
        lines = list(journal.lines.order_by("id"))
        self.assertEqual(lines[0].account, self.liability)
        self.assertEqual(lines[0].debit, Decimal("12500.00"))
        self.assertEqual(lines[1].account, self.cash)
        self.assertEqual(lines[1].credit, Decimal("12500.00"))

    def test_refund_cannot_exceed_available_deposit(self):
        with self.assertRaises(ValidationError):
            refund_company_deposit(
                company=self.company, hotel=self.hotel,
                amount="1", method="CASH", user=self.user,
            )

    def test_company_from_another_hotel_is_rejected(self):
        other_org = Organization.objects.create(name="Other Corporate Deposit Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Corporate Deposit Hotel",
        )
        with self.assertRaises(ValidationError):
            receive_company_deposit(
                company=self.company, hotel=other_hotel,
                amount="1000", method="CASH", user=self.user,
            )
