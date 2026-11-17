from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import Company, CompanyDocument, CompanyDocumentLine, GroupReservation
from inventory.models import Department, Hotel, Organization


class CorporateDocumentWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        org = Organization.objects.create(name="Corporate Documents Org")
        cls.hotel = Hotel.objects.create(organization=org, name="Corporate Documents Hotel")
        cls.department = Department.objects.create(
            hotel=cls.hotel, code="CDOC", name="Corporate Documents Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="corporate_documents_user", password="test-password",
            role="FRONTDESK", hotel=cls.hotel, department=cls.department,
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel, name="IQ Tech Limited",
            billing_address="12 Technology Avenue, Kano",
            contact_person="Amina Bello", phone="08012345678",
            email="accounts@iqtech.example",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel, company=cls.company, reference="IQTECH-GRP-001",
            name="IQ Tech Staff Accommodation",
            check_in_date=date(2027, 4, 1), check_out_date=date(2027, 4, 5),
            status="DRAFT",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _document_post(self, **overrides):
        data = {
            "document_type": "QUOTATION",
            "issue_date": "2027-03-01",
            "valid_until": "2027-03-15",
            "terms": "Payment due before arrival.",
            "notes": "Thank you for choosing City Center Hotels.",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-description": "Accommodation — 20 rooms for 4 nights",
            "lines-0-quantity": "80",
            "lines-0-unit": "room nights",
            "lines-0-unit_price": "35000.00",
            "lines-1-description": "Breakfast",
            "lines-1-quantity": "80",
            "lines-1-unit": "guest breakfasts",
            "lines-1-unit_price": "3000.00",
        }
        data.update(overrides)
        return data

    def test_create_quotation_saves_manual_quantities_and_calculates_total(self):
        response = self.client.post(
            reverse("frontdesk_company_document_create", args=[self.group.pk]),
            self._document_post(),
        )
        document = CompanyDocument.objects.get(group_reservation=self.group)
        self.assertRedirects(
            response,
            reverse("frontdesk_company_document_detail", args=[document.pk]),
        )
        self.assertEqual(document.document_type, "QUOTATION")
        self.assertEqual(document.status, "DRAFT")
        self.assertEqual(document.subtotal, Decimal("3040000.00"))
        self.assertEqual(document.total_amount, Decimal("3040000.00"))
        self.assertEqual(document.lines.count(), 2)
        self.assertEqual(document.lines.get(description="Breakfast").quantity, Decimal("80.00"))
        self.assertTrue(document.document_number.startswith("QT-2027-"))

    def test_issue_locks_document_and_preserves_company_snapshot(self):
        self.client.post(
            reverse("frontdesk_company_document_create", args=[self.group.pk]),
            self._document_post(),
        )
        document = CompanyDocument.objects.get(group_reservation=self.group)
        self.company.name = "IQ Tech Renamed"
        self.company.save(update_fields=["name"])
        response = self.client.post(
            reverse("frontdesk_company_document_detail", args=[document.pk]),
            {"action": "ISSUE"},
        )
        self.assertRedirects(
            response,
            reverse("frontdesk_company_document_detail", args=[document.pk]),
        )
        document.refresh_from_db()
        self.assertEqual(document.status, "ISSUED")
        self.assertIsNotNone(document.issued_at)
        self.assertEqual(document.company_name_snapshot, "IQ Tech Limited")

        edit_response = self.client.get(
            reverse("frontdesk_company_document_edit", args=[self.group.pk, document.pk])
        )
        self.assertRedirects(
            edit_response,
            reverse("frontdesk_company_document_detail", args=[document.pk]),
        )

    def test_issued_quotation_converts_to_separate_proforma_with_copied_lines(self):
        self.client.post(
            reverse("frontdesk_company_document_create", args=[self.group.pk]),
            self._document_post(),
        )
        quotation = CompanyDocument.objects.get(group_reservation=self.group)
        self.client.post(
            reverse("frontdesk_company_document_detail", args=[quotation.pk]),
            {"action": "ISSUE"},
        )
        response = self.client.post(
            reverse("frontdesk_company_document_detail", args=[quotation.pk]),
            {"action": "CONVERT"},
        )
        proforma = CompanyDocument.objects.get(document_type="PROFORMA")
        self.assertRedirects(
            response,
            reverse("frontdesk_company_document_detail", args=[proforma.pk]),
        )
        self.assertEqual(proforma.status, "DRAFT")
        self.assertEqual(proforma.converted_from_id, quotation.pk)
        self.assertEqual(proforma.total_amount, quotation.total_amount)
        self.assertEqual(
            list(proforma.lines.values_list("description", "quantity", "unit_price")),
            list(quotation.lines.values_list("description", "quantity", "unit_price")),
        )
        self.assertEqual(quotation.status, "ISSUED")

    def test_only_issued_quotation_can_be_converted(self):
        self.client.post(
            reverse("frontdesk_company_document_create", args=[self.group.pk]),
            self._document_post(),
        )
        quotation = CompanyDocument.objects.get(group_reservation=self.group)
        response = self.client.post(
            reverse("frontdesk_company_document_detail", args=[quotation.pk]),
            {"action": "CONVERT"},
        )
        self.assertRedirects(
            response,
            reverse("frontdesk_company_document_detail", args=[quotation.pk]),
        )
        self.assertFalse(CompanyDocument.objects.filter(document_type="PROFORMA").exists())

    def test_document_pdf_is_downloadable(self):
        self.client.post(
            reverse("frontdesk_company_document_create", args=[self.group.pk]),
            self._document_post(),
        )
        document = CompanyDocument.objects.get(group_reservation=self.group)
        response = self.client.get(
            reverse("frontdesk_company_document_pdf", args=[document.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_company_document_is_hotel_scoped(self):
        other_org = Organization.objects.create(name="Other Corporate Docs Org")
        other_hotel = Hotel.objects.create(organization=other_org, name="Other Corporate Docs Hotel")
        other_company = Company.objects.create(hotel=other_hotel, name="Other Company")
        other_group = GroupReservation.objects.create(
            hotel=other_hotel, company=other_company, reference="OTHER-DOC-GROUP",
            check_in_date=date(2027, 4, 1), check_out_date=date(2027, 4, 2),
        )
        from billing.services.company_documents import create_company_document
        document = create_company_document(
            group=other_group,
            document_type="QUOTATION",
            user=self.user,
            lines=[{
                "description": "Room",
                "quantity": Decimal("1"),
                "unit": "night",
                "unit_price": Decimal("100"),
            }],
        )
        response = self.client.get(
            reverse("frontdesk_company_document_detail", args=[document.pk])
        )
        self.assertEqual(response.status_code, 404)
