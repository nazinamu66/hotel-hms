from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import Company, GroupReservation
from inventory.models import Department, Hotel, Organization


class CorporateFrontdeskViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        org = Organization.objects.create(name="Corporate Frontdesk Org")
        cls.hotel = Hotel.objects.create(organization=org, name="Corporate Frontdesk Hotel")
        cls.department = Department.objects.create(
            hotel=cls.hotel, code="CFD", name="Corporate Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="corporate_frontdesk", password="test-password",
            role="FRONTDESK", hotel=cls.hotel, department=cls.department,
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel, name="North Star Limited",
            contact_person="Amina Bello", phone="08000000000",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel, company=cls.company, reference="CORP-TEST-001",
            check_in_date=date(2027, 4, 1), check_out_date=date(2027, 4, 4),
            status="CONFIRMED",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_company_register_page_lists_hotel_companies(self):
        response = self.client.get(reverse("frontdesk_company_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "North Star Limited")

    def test_company_create_assigns_current_hotel_and_creator(self):
        response = self.client.post(reverse("frontdesk_company_create"), {
            "name": "Northern Logistics",
            "registration_number": "RC-123",
            "contact_person": "Musa",
            "phone": "08011111111",
            "email": "travel@example.test",
            "billing_address": "Kano",
            "tax_number": "",
            "notes": "",
            "is_active": "on",
        })
        self.assertRedirects(response, reverse("frontdesk_company_list"))
        company = Company.objects.get(name="Northern Logistics")
        self.assertEqual(company.hotel, self.hotel)
        self.assertEqual(company.created_by, self.user)

    def test_company_edit_is_hotel_scoped(self):
        response = self.client.post(
            reverse("frontdesk_company_edit", args=[self.company.pk]),
            {
                "name": "North Star Holdings",
                "registration_number": "",
                "contact_person": "Amina Bello",
                "phone": "08000000000",
                "email": "",
                "billing_address": "",
                "tax_number": "",
                "notes": "",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("frontdesk_company_list"))
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "North Star Holdings")

    def test_group_dashboard_shows_hotel_group_bookings(self):
        response = self.client.get(reverse("frontdesk_group_reservations"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CORP-TEST-001")
        self.assertContains(response, "North Star Limited")

    def test_company_from_another_hotel_is_not_editable(self):
        other_org = Organization.objects.create(name="Other Corporate Org")
        other_hotel = Hotel.objects.create(organization=other_org, name="Other Corporate Hotel")
        other_company = Company.objects.create(hotel=other_hotel, name="Other Company")
        response = self.client.get(
            reverse("frontdesk_company_edit", args=[other_company.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_group_dashboard_does_not_show_other_hotel_bookings(self):
        other_org = Organization.objects.create(name="Other Group Org")
        other_hotel = Hotel.objects.create(organization=other_org, name="Other Group Hotel")
        other_company = Company.objects.create(hotel=other_hotel, name="Other Group Company")
        GroupReservation.objects.create(
            hotel=other_hotel, company=other_company, reference="OTHER-GROUP-001",
            check_in_date=date(2027, 4, 1), check_out_date=date(2027, 4, 2),
        )
        response = self.client.get(reverse("frontdesk_group_reservations"))
        self.assertNotContains(response, "OTHER-GROUP-001")
