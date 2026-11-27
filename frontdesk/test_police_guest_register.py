from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Folio, Guest
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory


class PoliceGuestRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Police Register Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Police Register Test Hotel",
        )
        department = Department.objects.create(
            hotel=cls.hotel,
            code="PGR",
            name="Police Register Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="police_register_frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=department,
        )
        cls.category = RoomCategory.objects.create(
            hotel=cls.hotel,
            name="Police Register Category",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Amina",
            last_name="Guest",
            nationality="Nigerian",
            id_number="NGA-123456",
            phone="08030000000",
        )
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_number="PGR-01",
            category=self.category,
            status="OCCUPIED",
        )
        self.active_folio = Folio.objects.create(
            folio_type="ROOM",
            hotel=self.hotel,
            room=self.room,
            guest=self.guest,
            check_out_at=timezone.now() + timedelta(days=2),
        )

    def test_register_lists_active_checked_in_guest_and_standard_fields(self):
        response = self.client.get(reverse("frontdesk_police_guest_register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amina Guest")
        self.assertContains(response, "Nigerian")
        self.assertContains(response, "NGA-123456")
        self.assertContains(response, "08030000000")
        self.assertContains(response, "PGR-01")
        self.assertContains(response, "Expected departure")
        self.assertContains(response, "Print / Save as PDF")

    def test_register_excludes_checked_out_stays(self):
        closed_guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Checked",
            last_name="Out",
        )
        closed_room = Room.objects.create(
            hotel=self.hotel,
            room_number="PGR-02",
            category=self.category,
            status="AVAILABLE",
        )
        Folio.objects.create(
            folio_type="ROOM",
            hotel=self.hotel,
            room=closed_room,
            guest=closed_guest,
            is_closed=True,
        )

        response = self.client.get(reverse("frontdesk_police_guest_register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amina Guest")
        self.assertNotContains(response, "Checked Out")

    def test_register_flags_missing_identity_number(self):
        self.guest.id_number = ""
        self.guest.save(update_fields=["id_number"])

        response = self.client.get(reverse("frontdesk_police_guest_register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Identity numbers missing")
        self.assertContains(response, "Not recorded")
        self.assertContains(response, "active stay(s) have no recorded guest identity number")

    def test_register_is_limited_to_the_users_hotel(self):
        other_org = Organization.objects.create(name="Other Police Register Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Police Register Hotel",
        )
        other_category = RoomCategory.objects.create(
            hotel=other_hotel,
            name="Other Police Register Category",
        )
        other_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Foreign",
            last_name="Guest",
            id_number="FOREIGN-1",
        )
        other_room = Room.objects.create(
            hotel=other_hotel,
            room_number="OTHER-01",
            category=other_category,
            status="OCCUPIED",
        )
        Folio.objects.create(
            folio_type="ROOM",
            hotel=other_hotel,
            room=other_room,
            guest=other_guest,
        )

        response = self.client.get(reverse("frontdesk_police_guest_register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amina Guest")
        self.assertNotContains(response, "Foreign Guest")
