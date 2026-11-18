from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from billing.models import Folio, Guest, Reservation, ReservationRoomAllocation
from billing.services.complimentary_rooms import request_complimentary_nights
from frontdesk.workflows.reservation import create_reservation
from frontdesk.workflows.reservation_allocations import (
    check_in_room_allocation,
    mark_room_allocation_no_show,
    update_room_allocation,
)
from frontdesk.workflows.checkout import checkout_guest
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory, RoomRate


class OrdinaryMultiRoomReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Multi Room Reservation Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Multi Room Reservation Hotel",
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="MRF",
            name="Multi Room Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="multi_room_frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.department,
        )
        cls.primary_guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Primary",
            last_name="Guest",
        )
        cls.other_guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Second",
            last_name="Occupant",
        )
        cls.category = RoomCategory.objects.create(name="Multi Room Category")

    def setUp(self):
        self.room_one = Room.objects.create(
            hotel=self.hotel,
            room_number="MR-01",
            category=self.category,
            status="AVAILABLE",
        )
        self.room_two = Room.objects.create(
            hotel=self.hotel,
            room_number="MR-02",
            category=self.category,
            status="AVAILABLE",
        )

    def make_reservation(self):
        return create_reservation(
            hotel=self.hotel,
            user=self.user,
            guest_id=self.primary_guest.pk,
            category_id=self.category.pk,
            number_of_rooms=2,
            check_in=date(2027, 6, 1),
            check_out=date(2027, 6, 4),
        )

    def test_creation_makes_one_allocation_per_room_with_primary_guest_default(self):
        reservation = self.make_reservation()

        allocations = list(reservation.room_allocations.order_by("allocation_number"))

        self.assertEqual(reservation.number_of_rooms, 2)
        self.assertEqual(len(allocations), 2)
        self.assertEqual([item.allocation_number for item in allocations], [1, 2])
        self.assertTrue(all(item.guest_id == self.primary_guest.pk for item in allocations))
        self.assertTrue(all(item.status == "RESERVED" for item in allocations))

    def test_occupant_and_room_can_be_assigned_independently(self):
        reservation = self.make_reservation()
        second = reservation.room_allocations.get(allocation_number=2)

        updated = update_room_allocation(
            allocation_id=second.pk,
            hotel=self.hotel,
            guest_id=self.other_guest.pk,
            room_id=self.room_two.pk,
        )

        self.assertEqual(updated.guest_id, self.other_guest.pk)
        self.assertEqual(updated.room_id, self.room_two.pk)

    def test_cross_hotel_occupant_is_rejected(self):
        reservation = self.make_reservation()
        second = reservation.room_allocations.get(allocation_number=2)
        other_org = Organization.objects.create(name="Other Multi Room Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Multi Room Hotel",
        )
        foreign_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Foreign",
            last_name="Guest",
        )

        with self.assertRaises(ValidationError):
            update_room_allocation(
                allocation_id=second.pk,
                hotel=self.hotel,
                guest_id=foreign_guest.pk,
            )


    @patch("billing.models.Folio.apply_daily_room_charge")
    def test_complimentary_request_is_bound_to_one_allocation_and_its_folio(self, apply_charge):
        RoomRate.objects.create(category=self.category, price_per_night="50000.00")
        reservation = self.make_reservation()
        first, second = list(reservation.room_allocations.order_by("allocation_number"))

        waiver = request_complimentary_nights(
            reservation=reservation,
            allocation=second,
            start_date=date(2027, 6, 1),
            end_date=date(2027, 6, 1),
            reason="Director approved this room only",
            user=self.user,
        )[0]

        self.assertEqual(waiver.allocation_id, second.pk)
        self.assertIsNone(waiver.folio_id)

        first, first_room, first_folio = check_in_room_allocation(first.pk, self.hotel, self.user)
        waiver.refresh_from_db()
        self.assertIsNone(waiver.folio_id)

        second, second_room, second_folio = check_in_room_allocation(second.pk, self.hotel, self.user)
        waiver.refresh_from_db()
        self.assertEqual(waiver.folio_id, second_folio.pk)
        self.assertNotEqual(waiver.folio_id, first_folio.pk)

    def test_same_night_can_be_requested_for_two_different_allocations(self):
        RoomRate.objects.create(category=self.category, price_per_night="50000.00")
        reservation = self.make_reservation()
        first, second = list(reservation.room_allocations.order_by("allocation_number"))

        first_waiver = request_complimentary_nights(
            reservation=reservation, allocation=first,
            start_date=date(2027, 6, 1), end_date=date(2027, 6, 1),
            reason="Approved for first room", user=self.user,
        )[0]
        second_waiver = request_complimentary_nights(
            reservation=reservation, allocation=second,
            start_date=date(2027, 6, 1), end_date=date(2027, 6, 1),
            reason="Approved for second room", user=self.user,
        )[0]

        self.assertNotEqual(first_waiver.pk, second_waiver.pk)
        self.assertEqual(
            ReservationRoomAllocation.objects.filter(
                reservation=reservation,
                complimentary_room_nights__room_night_date=date(2027, 6, 1),
            ).count(),
            2,
        )

    def test_ordinary_multi_room_complimentary_request_requires_allocation(self):
        RoomRate.objects.create(category=self.category, price_per_night="50000.00")
        reservation = self.make_reservation()
        with self.assertRaises(ValidationError):
            request_complimentary_nights(
                reservation=reservation,
                start_date=date(2027, 6, 1),
                end_date=date(2027, 6, 1),
                reason="Must select a room",
                user=self.user,
            )

    def test_no_show_releases_only_selected_room_and_keeps_reservation_active(self):
        reservation = self.make_reservation()
        first, second = list(reservation.room_allocations.order_by("allocation_number"))
        first.room = self.room_one
        first.save(update_fields=["room"])

        marked = mark_room_allocation_no_show(first.pk, self.hotel)
        reservation.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(marked.status, "NO_SHOW")
        self.assertEqual(first.status, "NO_SHOW")
        self.assertEqual(second.status, "RESERVED")
        self.assertEqual(reservation.status, "RESERVED")
        self.assertEqual(first.room_id, self.room_one.pk)

        from frontdesk.services import get_available_rooms
        available = get_available_rooms(
            self.hotel, self.category, date(2027, 6, 1), date(2027, 6, 4),
        )
        self.assertTrue(available.filter(pk=self.room_one.pk).exists())

    def test_allocation_no_show_does_not_prevent_sibling_check_in(self):
        reservation = self.make_reservation()
        first, second = list(reservation.room_allocations.order_by("allocation_number"))

        mark_room_allocation_no_show(first.pk, self.hotel)
        second, room, folio = check_in_room_allocation(second.pk, self.hotel, self.user)

        reservation.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(first.status, "NO_SHOW")
        self.assertEqual(second.status, "CHECKED_IN")
        self.assertEqual(reservation.status, "CHECKED_IN")

    def test_all_allocations_no_show_sets_master_reservation_no_show(self):
        reservation = self.make_reservation()
        allocations = list(reservation.room_allocations.order_by("allocation_number"))

        mark_room_allocation_no_show(allocations[0].pk, self.hotel)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "RESERVED")

        mark_room_allocation_no_show(allocations[1].pk, self.hotel)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "NO_SHOW")

    def test_checked_in_allocation_cannot_be_marked_no_show(self):
        reservation = self.make_reservation()
        allocation = reservation.room_allocations.first()
        allocation, room, folio = check_in_room_allocation(
            allocation.pk, self.hotel, self.user
        )

        with self.assertRaises(ValidationError):
            mark_room_allocation_no_show(allocation.pk, self.hotel)

    @patch("billing.models.Folio.apply_daily_room_charge")
    def test_rooms_can_check_in_and_check_out_independently(self, apply_charge):
        reservation = self.make_reservation()
        first, second = list(reservation.room_allocations.order_by("allocation_number"))

        first, first_room, first_folio = check_in_room_allocation(
            first.pk, self.hotel, self.user
        )
        reservation.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "CHECKED_IN")
        self.assertEqual(second.status, "RESERVED")
        self.assertEqual(reservation.status, "CHECKED_IN")

        second, second_room, second_folio = check_in_room_allocation(
            second.pk, self.hotel, self.user
        )
        self.assertNotEqual(first_room.pk, second_room.pk)
        self.assertNotEqual(first_folio.pk, second_folio.pk)

        checkout_guest(first_room, self.user)
        reservation.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "CHECKED_OUT")
        self.assertEqual(second.status, "CHECKED_IN")
        self.assertEqual(reservation.status, "CHECKED_IN")

        checkout_guest(second_room, self.user)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "CHECKED_OUT")
        self.assertEqual(
            ReservationRoomAllocation.objects.filter(
                reservation=reservation,
                status="CHECKED_OUT",
            ).count(),
            2,
        )
