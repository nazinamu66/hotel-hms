from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from billing.services.group_reservations import sync_group_reservation_status


class AllocationQuery:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def exclude(self, **kwargs):
        field, value = next(iter(kwargs.items()))
        return AllocationQuery([status for status in self.statuses if status != value])

    def filter(self, **kwargs):
        field, value = next(iter(kwargs.items()))
        return AllocationQuery([status for status in self.statuses if status == value])

    def exists(self):
        return bool(self.statuses)


class GroupReservationStatusTests(SimpleTestCase):
    def group(self, statuses, current="CONFIRMED"):
        group = SimpleNamespace(
            status=current,
            room_allocations=AllocationQuery(statuses),
            save=Mock(),
        )
        return group

    def test_first_arrival_moves_group_to_in_house_without_completing_others(self):
        group = self.group(["CHECKED_IN", "RESERVED", "RESERVED"])

        sync_group_reservation_status(group)

        self.assertEqual(group.status, "IN_HOUSE")
        group.save.assert_called_once_with(update_fields=["status"])

    def test_group_completes_only_after_every_non_cancelled_occupant_checks_out(self):
        group = self.group(["CHECKED_OUT", "CHECKED_OUT", "CANCELLED"], current="IN_HOUSE")

        sync_group_reservation_status(group)

        self.assertEqual(group.status, "COMPLETED")
        group.save.assert_called_once_with(update_fields=["status"])

    def test_reserved_occupants_keep_group_status_unchanged(self):
        group = self.group(["CHECKED_OUT", "RESERVED"], current="IN_HOUSE")

        sync_group_reservation_status(group)

        self.assertEqual(group.status, "IN_HOUSE")
        group.save.assert_not_called()

    def test_all_cancelled_group_is_not_marked_completed_automatically(self):
        group = self.group(["CANCELLED", "CANCELLED"])

        sync_group_reservation_status(group)

        self.assertEqual(group.status, "CONFIRMED")
        group.save.assert_not_called()
