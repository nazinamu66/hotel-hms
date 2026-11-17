from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import Account, JournalEntry
from billing.models import (
    Charge, Company, CompanyDeposit, CompanyDepositTransaction,
    CompanyInvoice, CompanyInvoiceCharge, CompanyInvoicePayment,
    Folio, GroupReservation, GroupReservationRoom, Guest,
)
from billing.services.company_deposit import receive_company_deposit
from billing.services.company_invoices import (
    apply_company_deposit,
    create_company_invoice,
    issue_company_invoice,
    record_company_invoice_payment,
)
from inventory.models import Department, Hotel, Organization
from rooms.models import RoomCategory


class CompanyInvoiceServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organization.objects.create(name="Company Invoice Test Org")
        cls.hotel = Hotel.objects.create(organization=org, name="Company Invoice Test Hotel")
        cls.company = Company.objects.create(hotel=cls.hotel, name="Invoice Test Company")
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel, company=cls.company, reference="INV-GROUP-001",
            check_in_date=date(2026, 12, 1), check_out_date=date(2026, 12, 3),
            status="COMPLETED",
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel, code="INV", name="Invoice Test Frontdesk",
            department_type="FRONTDESK",
        )
        cls.category = RoomCategory.objects.create(name="Invoice Test Standard")

    def setUp(self):
        guest = Guest.objects.create(
            hotel=self.hotel, first_name="Test", last_name="Occupant",
        )
        folio = Folio.objects.create(hotel=self.hotel, folio_type="WALKIN", guest=guest)
        allocation = GroupReservationRoom.objects.create(
            group_reservation=self.group, guest=guest, room_category=self.category,
            folio=folio, check_in_date=date(2026, 12, 1), check_out_date=date(2026, 12, 3),
        )
        self.charge = Charge.objects.create(
            folio=folio, department=self.department, description="Negotiated room night",
            amount=Decimal("70000.00"), billing_party="COMPANY",
            group_room_allocation=allocation,
        )
        self.folio = folio

    def test_invoice_uses_existing_company_charges_without_duplicate_journal(self):
        before = JournalEntry.objects.filter(hotel=self.hotel).count()
        invoice = create_company_invoice(group=self.group, user=None)
        self.assertEqual(invoice.total_amount, Decimal("70000.00"))
        self.assertEqual(invoice.charge_lines.count(), 1)
        issue_company_invoice(invoice=invoice, user=None)
        self.assertEqual(JournalEntry.objects.filter(hotel=self.hotel).count(), before)
        self.assertEqual(invoice.balance_due, Decimal("70000.00"))

    def test_charge_cannot_be_invoiced_twice(self):
        create_company_invoice(group=self.group, user=None)
        with self.assertRaises(ValidationError):
            create_company_invoice(group=self.group, user=None)

    def test_deposit_application_reduces_liability_and_receivable(self):
        invoice = create_company_invoice(group=self.group, user=None)
        issue_company_invoice(invoice=invoice, user=None)
        deposit, _ = receive_company_deposit(
            company=self.company, hotel=self.hotel, amount="30000",
            method="CASH", user=None,
        )
        invoice, application = apply_company_deposit(
            invoice=invoice, amount="20000", user=None, note="Part payment from advance",
        )
        deposit.refresh_from_db()
        self.assertEqual(deposit.balance, Decimal("10000.00"))
        self.assertEqual(application.transaction_type, "APPLIED")
        self.assertEqual(invoice.balance_due, Decimal("50000.00"))
        self.assertTrue(JournalEntry.objects.filter(reference=f"CORP-INV-DEP-{application.pk}").exists())

    def test_direct_transfer_payment_posts_cash_bank_against_receivable(self):
        invoice = create_company_invoice(group=self.group, user=None)
        issue_company_invoice(invoice=invoice, user=None)
        bank = Account.objects.create(
            hotel=self.hotel, code="INV-BANK", name="Invoice Test Bank",
            account_type="bank",
        )
        from accounting.models import BankAccount
        bank_account = BankAccount.objects.create(
            hotel=self.hotel, account=bank, bank_name="Test Bank",
            account_name="Hotel Account", account_number="123456",
        )
        invoice, payment = record_company_invoice_payment(
            invoice=invoice, amount="15000", method="TRANSFER", user=None,
            reference="INV-TRANSFER-001", bank_account=bank_account,
        )
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertEqual(invoice.balance_due, Decimal("55000.00"))
        self.assertTrue(JournalEntry.objects.filter(reference=f"CORP-INV-PAY-{payment.pk}").exists())

    def test_overpayment_is_rejected(self):
        invoice = create_company_invoice(group=self.group, user=None)
        issue_company_invoice(invoice=invoice, user=None)
        with self.assertRaises(ValidationError):
            record_company_invoice_payment(
                invoice=invoice, amount="70001", method="CASH", user=None,
            )

    def test_individual_charge_is_never_included(self):
        individual = Charge.objects.create(
            folio=self.folio, department=self.department,
            description="Personal minibar", amount=Decimal("5000.00"),
            billing_party="INDIVIDUAL",
        )
        invoice = create_company_invoice(group=self.group, user=None)
        self.assertEqual(invoice.total_amount, Decimal("70000.00"))
        self.assertFalse(invoice.charge_lines.filter(charge=individual).exists())
