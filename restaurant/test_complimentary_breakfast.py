from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.models import Charge, Folio, Guest
from inventory.models import Department, Hotel, Organization
from restaurant.models import ComplimentaryBreakfastRedemption, POSOrder
from restaurant.workflows.complimentary_breakfast import eligible_breakfast_charges
from rooms.models import Room, RoomCategory


class ComplimentaryBreakfastEligibilityTests(TestCase):
    def setUp(self):
        self.breakfast_date = date(2026, 11, 17)
        self.room_night_date = date(2026, 11, 16)
        organization = Organization.objects.create(name="Breakfast Test Organization")
        self.hotel = Hotel.objects.create(
            organization=organization,
            name="Breakfast Test Hotel",
        )
        self.restaurant = Department.objects.create(
            hotel=self.hotel,
            code="BR",
            name="Breakfast Restaurant",
            department_type="RESTAURANT",
        )
        self.category = RoomCategory.objects.create(
            hotel=self.hotel, name="Breakfast Test Standard"
        )
        self.guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Test",
            last_name="Guest",
        )
        self.user = get_user_model().objects.create_user(
            username="breakfast_auditor",
            password="test-password",
            role="RESTAURANT",
            hotel=self.hotel,
            department=self.restaurant,
        )

    def make_room_charge(
        self,
        room_number,
        *,
        rate_source="STANDARD",
        room_rate="100.00",
        original_rate="100.00",
        discount="0.00",
        closed=False,
    ):
        room = Room.objects.create(
            hotel=self.hotel,
            room_number=room_number,
            category=self.category,
        )
        folio = Folio.objects.create(
            folio_type="ROOM",
            hotel=self.hotel,
            room=room,
            guest=self.guest,
            is_closed=closed,
        )
        charge = Charge.objects.create(
            folio=folio,
            description="Room charge",
            department=self.restaurant,
            amount=Decimal(room_rate),
            room_night_date=self.room_night_date,
            room_rate=Decimal(room_rate),
            original_room_rate=Decimal(original_rate),
            room_rate_source=rate_source,
            discount_percentage=Decimal(discount),
        )
        return room, folio, charge

    def test_unpaid_full_published_rate_room_night_qualifies_next_morning(self):
        _room, _folio, charge = self.make_room_charge("101")

        eligible = eligible_breakfast_charges(
            self.hotel, breakfast_date=self.breakfast_date
        )

        self.assertEqual([item.pk for item in eligible], [charge.pk])

    def test_discounted_room_night_does_not_qualify(self):
        self.make_room_charge(
            "102",
            room_rate="95.00",
            original_rate="100.00",
            discount="5.00",
        )

        eligible = eligible_breakfast_charges(
            self.hotel, breakfast_date=self.breakfast_date
        )

        self.assertEqual(eligible, [])

    def test_negotiated_rate_does_not_qualify_even_if_equal_to_published_rate(self):
        self.make_room_charge("103", rate_source="GROUP")

        eligible = eligible_breakfast_charges(
            self.hotel, breakfast_date=self.breakfast_date
        )

        self.assertEqual(eligible, [])

    def test_room_cannot_redeem_more_than_once_on_the_breakfast_date(self):
        room, folio, charge = self.make_room_charge("104")
        order = POSOrder.objects.create(
            department=self.restaurant,
            folio=folio,
            is_complimentary_breakfast=True,
        )
        ComplimentaryBreakfastRedemption.objects.create(
            folio=folio,
            room=room,
            source_charge=charge,
            order=order,
            room_night_date=self.room_night_date,
            breakfast_date=self.breakfast_date,
            redeemed_by=self.user,
        )

        eligible = eligible_breakfast_charges(
            self.hotel, breakfast_date=self.breakfast_date
        )

        self.assertEqual(eligible, [])
