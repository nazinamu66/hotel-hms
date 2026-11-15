from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.models import Company, GroupReservation, GroupReservationRoom, Guest, Reservation
from billing.services.group_reservations import add_group_occupant
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

