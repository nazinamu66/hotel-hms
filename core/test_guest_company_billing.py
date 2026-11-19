from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from accounting.models import Account, JournalEntry, JournalLine
from billing.models import (
    Charge,
    Company,
    CompanyDeposit,
    CompanyDepositTransaction,
    CompanyInvoicePayment,
    Folio,
    Guest,
    GuestDeposit,
    GuestDepositTransaction,
    Payment,
    Reservation,
    ReservationDeposit,
    GroupReservation,
    GroupReservationRoom,
)
from billing.services.company_invoices import create_company_invoice, issue_company_invoice
from core.services.guest_company_billing import build_guest_company_billing
from inventory.models import Department, Hotel, Organization
from rooms.models import RoomCategory


class GuestCompanyBillingServiceTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Billing Reporting Org")
        self.hotel = Hotel.objects.create(
            organization=organization,
            name="Billing Reporting Hotel",
        )
        self.outside_hotel = Hotel.objects.create(name="Outside Billing Reporting Hotel")
        self.department = Department.objects.create(
            hotel=self.hotel,
            code="BILL-RPT",
            name="Billing Reporting Frontdesk",
            department_type="FRONTDESK",
        )
        self.guest = Guest.objects.create(
            hotel=self.hotel, first_name="Ada", last_name="Guest"
        )
        self.folio = Folio.objects.create(
            hotel=self.hotel, folio_type="WALKIN", guest=self.guest
        )
        self.category = RoomCategory.objects.create(name="Billing Reporting Category")
        self.company = Company.objects.create(hotel=self.hotel, name="Billing Client Ltd")
        self.group = GroupReservation.objects.create(
            hotel=self.hotel,
            company=self.company,
            reference="BILL-RPT-001",
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 3),
        )
        self.allocation = GroupReservationRoom.objects.create(
            group_reservation=self.group,
            guest=self.guest,
            room_category=self.category,
            folio=self.folio,
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 3),
        )

    def _charge(self, amount, billing_party):
        return Charge.objects.create(
            folio=self.folio,
            department=self.department,
            description=f"{billing_party} charge",
            amount=Decimal(amount),
            billing_party=billing_party,
            group_room_allocation=self.allocation if billing_party == "COMPANY" else None,
        )

    def _post_ar(self, debit, credit):
        account = Account.objects.get(
            hotel=self.hotel, system_key="accounts_receivable"
        )
        journal = JournalEntry.objects.create(
            hotel=self.hotel,
            date=date(2026, 11, 19),
            description="Billing report reconciliation fixture",
            reference="BILL-RPT-AR",
        )
        if debit:
            JournalLine.objects.create(
                journal=journal, account=account, debit=Decimal(debit)
            )
        if credit:
            JournalLine.objects.create(
                journal=journal, account=account, credit=Decimal(credit)
            )

    def test_guest_and_company_receivables_reconcile_without_double_counting(self):
        self._charge("1000.00", "INDIVIDUAL")
        Payment.objects.create(
            folio=self.folio,
            amount=Decimal("300.00"),
            method="CASH",
            allocation_tracking_enabled=False,
        )
        self._charge("2000.00", "COMPANY")
        invoice = create_company_invoice(group=self.group, user=None)
        issue_company_invoice(invoice=invoice, user=None)
        CompanyInvoicePayment.objects.create(
            invoice=invoice,
            amount=Decimal("500.00"),
            method="CASH",
        )
        self._charge("400.00", "COMPANY")  # remains unbilled

        # AR = 1,000 - 300 + 2,000 - 500 + 400 = 2,600.
        self._post_ar("3400.00", "800.00")
        report = build_guest_company_billing(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["guest_receivables"], Decimal("700.00"))
        self.assertEqual(report["company_invoice_outstanding"], Decimal("1500.00"))
        self.assertEqual(report["unbilled_company_charges"], Decimal("400.00"))
        self.assertEqual(report["company_receivables"], Decimal("1900.00"))
        self.assertEqual(report["total_operational_receivables"], Decimal("2600.00"))
        self.assertEqual(report["ledger_accounts_receivable"], Decimal("2600.00"))
        self.assertEqual(report["receivables_reconciliation_difference"], Decimal("0.00"))

    def test_draft_invoice_does_not_remove_source_charge_from_receivables(self):
        self._charge("12500.00", "COMPANY")
        create_company_invoice(group=self.group, user=None)  # draft only

        report = build_guest_company_billing(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["company_invoice_outstanding"], Decimal("0.00"))
        self.assertEqual(report["unbilled_company_charges"], Decimal("12500.00"))
        self.assertEqual(report["company_receivables"], Decimal("12500.00"))

    def test_guest_deposit_guarantees_are_earmarked_not_deducted_twice(self):
        deposit = GuestDeposit.objects.create(
            hotel=self.hotel, guest=self.guest, balance=Decimal("1000.00")
        )
        reservation = Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 12, 1),
            check_out_date=date(2026, 12, 3),
        )
        ReservationDeposit.objects.create(
            reservation=reservation,
            guest_deposit=deposit,
            nightly_rate=Decimal("750.00"),
            number_of_rooms=1,
            number_of_nights=2,
            reservation_value=Decimal("1500.00"),
            required_amount=Decimal("300.00"),
            allocated_amount=Decimal("300.00"),
            cancellation_deadline=datetime(2026, 11, 30, tzinfo=timezone.utc),
            status="GUARANTEED",
        )
        CompanyDeposit.objects.create(
            hotel=self.hotel, company=self.company, balance=Decimal("2000.00")
        )

        report = build_guest_company_billing(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["guest_deposits_held"], Decimal("1000.00"))
        self.assertEqual(report["guest_guarantees_earmarked"], Decimal("300.00"))
        self.assertEqual(report["guest_deposits_available"], Decimal("700.00"))
        self.assertEqual(report["company_deposits_held"], Decimal("2000.00"))

    def test_collection_totals_exclude_internal_deposit_applications(self):
        Payment.objects.create(
            folio=self.folio,
            amount=Decimal("250.00"),
            method="CASH",
        )
        Payment.objects.create(
            folio=self.folio,
            amount=Decimal("100.00"),
            method="DEPOSIT",
        )
        guest_deposit = GuestDeposit.objects.create(
            hotel=self.hotel, guest=self.guest, balance=Decimal("500.00")
        )
        GuestDepositTransaction.objects.create(
            deposit=guest_deposit,
            transaction_type="RECEIVED",
            amount=Decimal("500.00"),
        )
        CompanyDeposit.objects.create(
            hotel=self.hotel, company=self.company, balance=Decimal("300.00")
        )
        CompanyDepositTransaction.objects.create(
            deposit=CompanyDeposit.objects.get(hotel=self.hotel, company=self.company),
            transaction_type="RECEIVED",
            amount=Decimal("300.00"),
            payment_method="CASH",
        )

        report = build_guest_company_billing(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            today=date(2026, 11, 19),
            collection_from=date(2026, 11, 1),
            collection_to=date(2026, 11, 30),
        )

        self.assertEqual(report["collections"]["guest_cash_bank"], Decimal("250.00"))
        self.assertEqual(report["collections"]["guest_deposit_received"], Decimal("500.00"))
        self.assertEqual(report["collections"]["company_deposit_received"], Decimal("300.00"))
        self.assertEqual(report["collection_total"], Decimal("1050.00"))

    def test_reporting_scope_excludes_other_hotels(self):
        outside_guest = Guest.objects.create(
            hotel=self.outside_hotel, first_name="Outside", last_name="Guest"
        )
        outside_folio = Folio.objects.create(
            hotel=self.outside_hotel, folio_type="WALKIN", guest=outside_guest
        )
        outside_department = Department.objects.create(
            hotel=self.outside_hotel,
            code="BILL-OUT",
            name="Outside Frontdesk",
            department_type="FRONTDESK",
        )
        Charge.objects.create(
            folio=outside_folio,
            department=outside_department,
            description="Outside receivable",
            amount=Decimal("90000.00"),
        )

        report = build_guest_company_billing(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["guest_receivables"], Decimal("0.00"))
        self.assertEqual(report["company_receivables"], Decimal("0.00"))
        self.assertEqual(report["ledger_accounts_receivable"], Decimal("0.00"))
