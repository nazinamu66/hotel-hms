from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import Folio, Guest
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory, RoomRate


class WalkInCheckInUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Walk-in Check-in Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Walk-in Check-in Hotel",
        )
        department = Department.objects.create(
            hotel=cls.hotel,
            code="WCI",
            name="Walk-in Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="walkin_frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=department,
        )
        cls.category = RoomCategory.objects.create(name="Walk-in Check-in Category")
        RoomRate.objects.create(
            category=cls.category,
            price_per_night=Decimal("45000.00"),
            currency="NGN",
        )
        cls.existing_guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Returning",
            last_name="Guest",
            phone="08030000000",
            email="returning@example.com",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_number="WCI-01",
            category=self.category,
            status="AVAILABLE",
        )
        self.url = reverse("frontdesk_check_in", args=[self.room.pk])

    def test_room_board_available_room_links_to_walk_in_check_in(self):
        response = self.client.get(reverse("frontdesk_room_board"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)

    def test_check_in_page_shows_room_rate_and_both_guest_options(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing guest")
        self.assertContains(response, "New guest")
        self.assertContains(response, "45000.00")
        self.assertContains(response, "guest_id")

    @patch("billing.models.Folio.apply_daily_room_charge")
    def test_walk_in_check_in_can_use_existing_guest(self, apply_charge):
        response = self.client.post(
            self.url,
            {
                "guest_id": str(self.existing_guest.pk),
                "expected_checkout": "",
                "room_discount_percent": "0",
                "room_discount_reason": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("frontdesk_active_stay", args=[self.room.pk]),
        )
        folio = Folio.objects.get(room=self.room, is_closed=False)
        self.assertEqual(folio.guest, self.existing_guest)
        apply_charge.assert_called_once()

    @patch("billing.models.Folio.apply_daily_room_charge")
    def test_walk_in_check_in_can_register_a_new_guest(self, apply_charge):
        response = self.client.post(
            self.url,
            {
                "first_name": "New",
                "last_name": "Arrival",
                "phone": "08031112222",
                "email": "new@example.com",
                "nationality": "Nigerian",
                "id_number": "NGA-123",
                "expected_checkout": "",
                "room_discount_percent": "0",
                "room_discount_reason": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("frontdesk_active_stay", args=[self.room.pk]),
        )
        guest = Guest.objects.get(hotel=self.hotel, phone="08031112222")
        folio = Folio.objects.get(room=self.room, is_closed=False)
        self.assertEqual(folio.guest, guest)
        self.assertEqual(guest.full_name, "New Arrival")
        apply_charge.assert_called_once()

    def test_walk_in_check_in_rejects_guest_from_another_hotel(self):
        other_org = Organization.objects.create(name="Foreign Walk-in Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Foreign Walk-in Hotel",
        )
        foreign_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Foreign",
            last_name="Guest",
        )

        response = self.client.post(
            self.url,
            {
                "guest_id": str(foreign_guest.pk),
                "room_discount_percent": "0",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Folio.objects.filter(room=self.room, is_closed=False).exists())

    def test_walk_in_check_in_requires_guest_selection_or_new_guest_name(self):
        response = self.client.post(
            self.url,
            {
                "room_discount_percent": "0",
                "first_name": "",
                "last_name": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an existing guest or enter")
        self.assertFalse(Folio.objects.filter(room=self.room, is_closed=False).exists())
