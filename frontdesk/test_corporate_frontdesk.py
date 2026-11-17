from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import Company, GroupReservation, GroupReservationRate, Guest, GroupReservationRoom
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory, RoomRate


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

    def test_group_dashboard_has_create_booking_action(self):
        response = self.client.get(reverse("frontdesk_group_reservations"))
        self.assertContains(
            response,
            reverse("frontdesk_group_reservation_create"),
        )

    def test_group_reservation_create_page_and_save(self):
        url = reverse("frontdesk_group_reservation_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create group reservation")

        response = self.client.post(url, {
            "company": self.company.pk,
            "name": "North Star Annual Meeting",
            "check_in_date": "2027-05-10",
            "check_out_date": "2027-05-13",
            "room_billing_party": "COMPANY",
            "agreement_notes": "Company covers room charges.",
        })
        self.assertRedirects(response, reverse("frontdesk_group_reservations"))
        group = GroupReservation.objects.get(name="North Star Annual Meeting")
        self.assertEqual(group.hotel, self.hotel)
        self.assertEqual(group.company, self.company)
        self.assertEqual(group.created_by, self.user)
        self.assertTrue(group.reference.startswith("GRP-"))

    def test_group_create_rejects_company_from_another_hotel(self):
        other_org = Organization.objects.create(name="Foreign Group Org")
        other_hotel = Hotel.objects.create(organization=other_org, name="Foreign Group Hotel")
        other_company = Company.objects.create(hotel=other_hotel, name="Foreign Company")
        response = self.client.post(
            reverse("frontdesk_group_reservation_create"),
            {
                "company": other_company.pk,
                "name": "Invalid cross-hotel group",
                "check_in_date": "2027-05-10",
                "check_out_date": "2027-05-13",
                "room_billing_party": "COMPANY",
                "agreement_notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            GroupReservation.objects.filter(name="Invalid cross-hotel group").exists()
        )

    def test_group_detail_shows_booking_and_occupant_action(self):
        response = self.client.get(
            reverse("frontdesk_group_reservation_detail", args=[self.group.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.group.reference)
        self.assertContains(response, "Add occupant")

    def test_add_new_group_occupant_creates_guest_reservation_and_allocation(self):
        category = RoomCategory.objects.create(name="Corporate Standard")
        Room.objects.create(
            hotel=self.hotel, room_number="CS-01", category=category,
        )
        response = self.client.post(
            reverse("frontdesk_group_occupant_add", args=[self.group.pk]),
            {
                "guest_mode": "NEW",
                "first_name": "Fatima",
                "last_name": "Yusuf",
                "phone": "08022223333",
                "email": "fatima@example.test",
                "nationality": "Nigerian",
                "id_number": "NIN-NEW-001",
                "room_category": category.pk,
                "room": "",
                "check_in_date": "2027-04-01",
                "check_out_date": "2027-04-04",
                "occupant_note": "",
            },
        )
        self.assertRedirects(
            response,
            reverse("frontdesk_group_reservation_detail", args=[self.group.pk]),
        )
        guest = Guest.objects.get(hotel=self.hotel, id_number="NIN-NEW-001")
        allocation = GroupReservationRoom.objects.get(
            group_reservation=self.group, guest=guest,
        )
        self.assertIsNotNone(allocation.reservation_id)
        self.assertEqual(allocation.reservation.guest_id, guest.pk)

    def test_new_group_occupant_warns_on_possible_duplicate_guest(self):
        category = RoomCategory.objects.create(name="Duplicate Check Category")
        Room.objects.create(hotel=self.hotel, room_number="DC-01", category=category)
        Guest.objects.create(
            hotel=self.hotel, first_name="Existing", last_name="Guest",
            phone="08033334444",
        )
        response = self.client.post(
            reverse("frontdesk_group_occupant_add", args=[self.group.pk]),
            {
                "guest_mode": "NEW", "first_name": "New", "last_name": "Person",
                "phone": "08033334444", "email": "", "nationality": "",
                "id_number": "", "room_category": category.pk, "room": "",
                "check_in_date": "2027-04-01", "check_out_date": "2027-04-04",
                "occupant_note": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Guest.objects.filter(hotel=self.hotel).count(), 1)

    def test_group_room_rates_can_be_saved_per_category(self):
        self.group.status = "DRAFT"
        self.group.save(update_fields=["status"])
        category = RoomCategory.objects.create(name="Corporate Deluxe")
        Room.objects.create(
            hotel=self.hotel, room_number="CD-01", category=category,
        )
        RoomRate.objects.create(category=category, price_per_night="50000.00")
        url = reverse("frontdesk_group_room_rates_update", args=[self.group.pk])
        response = self.client.post(url, {f"rate_{category.pk}": "42000.00"})
        self.assertRedirects(
            response,
            reverse("frontdesk_group_reservation_detail", args=[self.group.pk]),
        )
        rate = GroupReservationRate.objects.get(
            group_reservation=self.group, room_category=category,
        )
        self.assertEqual(rate.nightly_rate, Decimal("42000.00"))

    def test_blank_group_room_rate_removes_override(self):
        self.group.status = "DRAFT"
        self.group.save(update_fields=["status"])
        category = RoomCategory.objects.create(name="Corporate Economy")
        Room.objects.create(
            hotel=self.hotel, room_number="CE-01", category=category,
        )
        GroupReservationRate.objects.create(
            group_reservation=self.group,
            room_category=category,
            nightly_rate="30000.00",
        )
        response = self.client.post(
            reverse("frontdesk_group_room_rates_update", args=[self.group.pk]),
            {f"rate_{category.pk}": ""},
        )
        self.assertRedirects(
            response,
            reverse("frontdesk_group_reservation_detail", args=[self.group.pk]),
        )
        self.assertFalse(
            GroupReservationRate.objects.filter(
                group_reservation=self.group, room_category=category,
            ).exists()
        )

    def test_group_room_rates_are_locked_after_confirmation(self):
        category = RoomCategory.objects.create(name="Corporate Executive")
        Room.objects.create(
            hotel=self.hotel, room_number="CX-01", category=category,
        )
        self.group.status = "CONFIRMED"
        self.group.save(update_fields=["status"])
        self.client.post(
            reverse("frontdesk_group_room_rates_update", args=[self.group.pk]),
            {f"rate_{category.pk}": "61000.00"},
        )
        self.assertFalse(
            GroupReservationRate.objects.filter(
                group_reservation=self.group, room_category=category,
            ).exists()
        )

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
