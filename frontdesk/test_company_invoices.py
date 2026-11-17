from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import (
    Charge, Company, CompanyInvoice, Folio, GroupReservation,
    GroupReservationRoom, Guest,
)
from inventory.models import Department, Hotel, Organization
from rooms.models import RoomCategory


class CorporateInvoiceFrontdeskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        org = Organization.objects.create(name="Corporate Invoice UI Org")
        cls.hotel = Hotel.objects.create(organization=org, name="Corporate Invoice UI Hotel")
        cls.department = Department.objects.create(
            hotel=cls.hotel, code="CINV", name="Corporate Invoice Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="corporate_invoice_ui_user", password="test-password",
            role="FRONTDESK", hotel=cls.hotel, department=cls.department,
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel, name="IQ Tech Limited",
            billing_address="12 Technology Avenue, Kano",
            contact_person="Amina Bello", phone="08012345678",
            email="accounts@iqtech.example",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel, company=cls.company, reference="IQTECH-INVOICE-001",
            name="IQ Tech Staff Accommodation",
            check_in_date=date(2026, 12, 1), check_out_date=date(2026, 12, 5),
            status="COMPLETED",
        )
        cls.category = RoomCategory.objects.create(name="Corporate Invoice Standard")
        cls.frontdesk_department = cls.department

    def setUp(self):
        self.client.force_login(self.user)
        guest = Guest.objects.create(
            hotel=self.hotel, first_name="Test", last_name="Occupant",
        )
        folio = Folio.objects.create(
            hotel=self.hotel, folio_type="WALKIN", guest=guest,
        )
        allocation = GroupReservationRoom.objects.create(
            group_reservation=self.group,
            guest=guest,
            room_category=self.category,
            folio=folio,
            check_in_date=date(2026, 12, 1),
            check_out_date=date(2026, 12, 5),
        )
        self.charge = Charge.objects.create(
            folio=folio,
            department=self.department,
            description="Negotiated room accommodation",
            amount=Decimal("140000.00"),
            billing_party="COMPANY",
            group_room_allocation=allocation,
        )

    def test_group_page_exposes_final_invoice_workflow(self):
        response = self.client.get(
            reverse("frontdesk_group_reservation_detail", args=[self.group.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final company invoices")
        self.assertContains(
            response,
            reverse("frontdesk_company_invoice_create", args=[self.group.pk]),
        )

    def test_create_issue_and_download_company_invoice_pdf(self):
        response = self.client.post(
            reverse("frontdesk_company_invoice_create", args=[self.group.pk]),
            {"due_date": "2026-12-20", "notes": "Payment due within 15 days."},
        )
        invoice = CompanyInvoice.objects.get(group_reservation=self.group)
        self.assertRedirects(
            response,
            reverse("frontdesk_company_invoice_detail", args=[invoice.pk]),
        )
        self.assertEqual(invoice.status, "DRAFT")
        self.assertEqual(invoice.total_amount, Decimal("140000.00"))

        response = self.client.post(
            reverse("frontdesk_company_invoice_detail", args=[invoice.pk]),
            {"action": "ISSUE"},
        )
        self.assertRedirects(
            response,
            reverse("frontdesk_company_invoice_detail", args=[invoice.pk]),
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "ISSUED")

        detail = self.client.get(
            reverse("frontdesk_company_invoice_detail", args=[invoice.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, invoice.invoice_number)
        self.assertContains(detail, "Record direct payment")

        pdf = self.client.get(
            reverse("frontdesk_company_invoice_pdf", args=[invoice.pk])
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_individual_charge_is_not_added_to_company_invoice(self):
        Charge.objects.create(
            folio=self.charge.folio,
            department=self.department,
            description="Personal minibar",
            amount=Decimal("8500.00"),
            billing_party="INDIVIDUAL",
        )
        self.client.post(
            reverse("frontdesk_company_invoice_create", args=[self.group.pk]),
            {},
        )
        invoice = CompanyInvoice.objects.get(group_reservation=self.group)
        self.assertEqual(invoice.total_amount, Decimal("140000.00"))
        self.assertEqual(invoice.charge_lines.count(), 1)

    def test_invoice_is_scoped_to_current_hotel(self):
        response = self.client.post(
            reverse("frontdesk_company_invoice_create", args=[self.group.pk]),
            {},
        )
        invoice = CompanyInvoice.objects.get(group_reservation=self.group)

        other_org = Organization.objects.create(name="Other Corporate Invoice Org")
        other_hotel = Hotel.objects.create(
            organization=other_org, name="Other Corporate Invoice Hotel",
        )
        other_company = Company.objects.create(hotel=other_hotel, name="Other Company")
        other_group = GroupReservation.objects.create(
            hotel=other_hotel,
            company=other_company,
            reference="OTHER-CORP-INVOICE",
            check_in_date=date(2026, 12, 1),
            check_out_date=date(2026, 12, 2),
        )
        from billing.models import CompanyInvoice
        foreign_invoice = CompanyInvoice.objects.create(
            hotel=other_hotel,
            company=other_company,
            group_reservation=other_group,
            invoice_number="CI-2026-FOREIGN01",
            company_name_snapshot=other_company.name,
            group_reference_snapshot=other_group.reference,
            stay_check_in=other_group.check_in_date,
            stay_check_out=other_group.check_out_date,
        )
        foreign_invoice.full_clean()
        detail = self.client.get(
            reverse("frontdesk_company_invoice_detail", args=[foreign_invoice.pk])
        )
        self.assertEqual(detail.status_code, 404)
