from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.models import (
    Company,
    GroupReservation,
    GroupReservationRate,
    GroupReservationRoom,
    Guest,
    Reservation,
)
from billing.services.group_reservations import add_group_occupant
from billing.services.room_pricing import get_room_night_pricing
from inventory.models import Hotel, Organization
from rooms.models import Room, RoomCategory


class GroupOccupantReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Group Workflow Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Group Workflow Hotel",
        )
        cls.category = RoomCategory.objects.create(
            name="Group Workflow Standard",
        )
        cls.room = Room.objects.create(
            hotel=cls.hotel,
            room_number="G01",
            category=cls.category,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Amina",
            last_name="Yusuf",
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel,
            name="Group Workflow Ltd",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel,
            company=cls.company,
            reference="GROUP-WORKFLOW-001",
            check_in_date=date(2027, 1, 10),
            check_out_date=date(2027, 1, 15),
            status="CONFIRMED",
        )

    def test_add_occupant_creates_one_individual_reservation_and_link(self):
        allocation = add_group_occupant(
            group_reservation_id=self.group.pk,
            guest_id=self.guest.pk,
            room_category_id=self.category.pk,
            room_id=self.room.pk,
            user=None,
        )

        self.assertEqual(allocation.group_reservation, self.group)
        self.assertEqual(allocation.guest, self.guest)
        self.assertEqual(allocation.room, self.room)
        self.assertEqual(allocation.status, "RESERVED")
        self.assertIsNotNone(allocation.reservation)
        self.assertEqual(allocation.reservation.guest, self.guest)
        self.assertEqual(allocation.reservation.number_of_rooms, 1)
        self.assertEqual(allocation.reservation.room, self.room)
        self.assertEqual(
            Reservation.objects.filter(
                group_allocation__group_reservation=self.group
            ).count(),
            1,
        )

    def test_rejects_guest_from_another_hotel(self):
        other_org = Organization.objects.create(name="Other Group Workflow Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Group Workflow Hotel",
        )
        other_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Other",
            last_name="Guest",
        )

        with self.assertRaises(ValidationError):
            add_group_occupant(
                group_reservation_id=self.group.pk,
                guest_id=other_guest.pk,
                room_category_id=self.category.pk,
                user=None,
            )

    def test_rejects_dates_outside_group_window(self):
        with self.assertRaises(ValidationError):
            add_group_occupant(
                group_reservation_id=self.group.pk,
                guest_id=self.guest.pk,
                room_category_id=self.category.pk,
                check_in_date=date(2027, 1, 9),
                user=None,
            )

    def test_group_allocation_clean_accepts_shared_room_category(self):
        allocation = GroupReservationRoom(
            group_reservation=self.group,
            guest=self.guest,
            room_category=self.category,
            room=self.room,
            check_in_date=self.group.check_in_date,
            check_out_date=self.group.check_out_date,
        )
        allocation.full_clean()



class GroupRoomPricingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Group Pricing Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Group Pricing Test Hotel",
        )
        cls.category = RoomCategory.objects.create(name="Group Pricing Standard")
        cls.room = Room.objects.create(
            hotel=cls.hotel,
            room_number="GP01",
            category=cls.category,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Hauwa",
            last_name="Abdullahi",
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel,
            name="Group Pricing Ltd",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel,
            company=cls.company,
            reference="GROUP-PRICING-001",
            check_in_date=date(2027, 2, 10),
            check_out_date=date(2027, 2, 15),
            status="CONFIRMED",
        )

    def _allocation(self, override=None):
        allocation = GroupReservationRoom(
            group_reservation=self.group,
            guest=self.guest,
            room_category=self.category,
            check_in_date=self.group.check_in_date,
            check_out_date=self.group.check_out_date,
            negotiated_nightly_rate=override,
        )
        return allocation

    def _folio_stub(self, allocation):
        return SimpleNamespace(
            group_allocation=allocation,
            room=SimpleNamespace(category=self.category),
        )

    def test_group_category_rate_is_selected_for_occupant(self):
        GroupReservationRate.objects.create(
            group_reservation=self.group,
            room_category=self.category,
            nightly_rate=Decimal("18500.00"),
        )
        allocation = self._allocation()

        result = get_room_night_pricing(self._folio_stub(allocation))

        self.assertEqual(result[0], allocation)
        self.assertEqual(result[1], Decimal("18500.00"))
        self.assertEqual(result[2], "GROUP")
        self.assertEqual(result[3], "COMPANY")

    def test_occupant_override_takes_precedence_over_group_rate(self):
        GroupReservationRate.objects.create(
            group_reservation=self.group,
            room_category=self.category,
            nightly_rate=Decimal("18500.00"),
        )
        allocation = self._allocation(Decimal("16000.00"))

        result = get_room_night_pricing(self._folio_stub(allocation))

        self.assertEqual(result[1], Decimal("16000.00"))
        self.assertEqual(result[2], "OVERRIDE")
        self.assertEqual(result[3], "COMPANY")

    def test_group_can_assign_room_nights_to_occupant(self):
        self.group.room_billing_party = "INDIVIDUAL"
        self.group.save(update_fields=["room_billing_party"])
        GroupReservationRate.objects.create(
            group_reservation=self.group,
            room_category=self.category,
            nightly_rate=Decimal("18500.00"),
        )

        result = get_room_night_pricing(
            self._folio_stub(self._allocation())
        )

        self.assertEqual(result[3], "INDIVIDUAL")
