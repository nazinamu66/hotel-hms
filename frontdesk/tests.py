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



from decimal import Decimal
from billing.models import CompanyInvoice
from frontdesk.company_invoice_views import _company_statement_metrics


class CompanyStatementMetricsTests(SimpleTestCase):
    def invoice(self, status, total, paid, balance):
        return SimpleNamespace(
            status=status,
            total_amount=Decimal(total),
            amount_paid=Decimal(paid),
            balance_due=Decimal(balance),
        )

    def test_statement_totals_include_issued_invoices_without_rewriting_them(self):
        issued_a = self.invoice("ISSUED", "60000.00", "60000.00", "0.00")
        issued_b = self.invoice("ISSUED", "30000.00", "30000.00", "0.00")
        draft = self.invoice("DRAFT", "12000.00", "0.00", "12000.00")
        void = self.invoice("VOID", "5000.00", "0.00", "5000.00")

        metrics = _company_statement_metrics(
            [issued_a, issued_b, draft, void],
            Decimal("15000.00"),
            Decimal("25000.00"),
        )

        self.assertEqual(metrics["invoiced_total"], Decimal("90000.00"))
        self.assertEqual(metrics["settled_total"], Decimal("90000.00"))
        self.assertEqual(metrics["outstanding_total"], Decimal("0.00"))
        self.assertEqual(metrics["unbilled_total"], Decimal("15000.00"))
        self.assertEqual(metrics["deposit_balance"], Decimal("25000.00"))
        self.assertEqual(issued_a.total_amount, Decimal("60000.00"))
        self.assertEqual(issued_b.total_amount, Decimal("30000.00"))

    def test_statement_handles_no_invoices_or_deposit(self):
        metrics = _company_statement_metrics([], Decimal("0.00"), Decimal("0.00"))

        self.assertEqual(metrics["invoiced_total"], Decimal("0.00"))
        self.assertEqual(metrics["settled_total"], Decimal("0.00"))
        self.assertEqual(metrics["outstanding_total"], Decimal("0.00"))
        self.assertEqual(metrics["unbilled_total"], Decimal("0.00"))
        self.assertEqual(metrics["deposit_balance"], Decimal("0.00"))



from django.core.exceptions import ValidationError
from frontdesk.workflows.check_in import validate_check_in


class CheckInRoomReadinessTests(SimpleTestCase):
    def test_only_available_rooms_pass_the_shared_readiness_guard(self):
        for status in (
            "OCCUPIED",
            "OCCUPIED_DIRTY",
            "VACANT_DIRTY",
            "OUT_OF_ORDER",
            "UNRECOGNIZED_STATUS",
        ):
            with self.subTest(status=status):
                room = SimpleNamespace(status=status)
                with self.assertRaises(ValidationError):
                    validate_check_in(room)

    def test_available_room_passes_the_shared_readiness_guard(self):
        room = SimpleNamespace(status="AVAILABLE")
        self.assertIsNone(validate_check_in(room))
