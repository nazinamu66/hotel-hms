from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase

from maintenance.views import start_ticket
from maintenance.models import MaintenanceMaterial
from decimal import Decimal
from maintenance.workflows.preventive import create_preventive_work_order


class StartTicketMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_does_not_start_a_maintenance_ticket(self):
        request = self.factory.get("/maintenance/tickets/123/start/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="MAINTENANCE",
        )

        response = start_ticket(request, ticket_id=123)

        self.assertEqual(response.status_code, 405)


class PreventiveWorkOrderLocationTests(TestCase):
    def setUp(self):
        self.user = SimpleNamespace(hotel_id=7)
        self.room = SimpleNamespace(room_number="101")
        self.location = SimpleNamespace(name="Generator Room")

    def make_schedule(self, *, room=None, location=None):
        asset = SimpleNamespace(
            is_active=True,
            hotel_id=7,
            room_id=1 if room else None,
            room=room,
            location_id=2 if location else None,
            location=location,
            name="Standby Generator",
        )
        return SimpleNamespace(
            is_active=True,
            asset=asset,
            name="Quarterly Generator Service",
            next_due_date=date(2026, 11, 21),
        )

    @patch("maintenance.workflows.preventive.MaintenanceScheduleCycle.objects.get_or_create")
    @patch("maintenance.workflows.preventive.MaintenanceTicket.objects.create")
    @patch("maintenance.workflows.preventive.MaintenanceTicket.objects.filter")
    def test_room_asset_creates_room_work_order(self, filter_tickets, create_ticket, get_cycle):
        get_cycle.return_value = (SimpleNamespace(work_order_id=None, save=lambda **kwargs: None), True)
        filter_tickets.return_value.first.return_value = None
        schedule = self.make_schedule(room=self.room)
        create_ticket.return_value = SimpleNamespace(pk=10)

        result = create_preventive_work_order(schedule, self.user)

        self.assertEqual(result.pk, 10)
        create_ticket.assert_called_once()
        kwargs = create_ticket.call_args.kwargs
        self.assertIs(kwargs["room"], self.room)
        self.assertIsNone(kwargs["location"])
        self.assertIs(kwargs["asset"], schedule.asset)
        self.assertIs(kwargs["schedule"], schedule)

    @patch("maintenance.workflows.preventive.MaintenanceScheduleCycle.objects.get_or_create")
    @patch("maintenance.workflows.preventive.MaintenanceTicket.objects.create")
    @patch("maintenance.workflows.preventive.MaintenanceTicket.objects.filter")
    def test_non_room_asset_creates_location_work_order(self, filter_tickets, create_ticket, get_cycle):
        get_cycle.return_value = (SimpleNamespace(work_order_id=None, save=lambda **kwargs: None), True)
        filter_tickets.return_value.first.return_value = None
        schedule = self.make_schedule(location=self.location)
        create_ticket.return_value = SimpleNamespace(pk=11)

        result = create_preventive_work_order(schedule, self.user)

        self.assertEqual(result.pk, 11)
        create_ticket.assert_called_once()
        kwargs = create_ticket.call_args.kwargs
        self.assertIsNone(kwargs["room"])
        self.assertIs(kwargs["location"], self.location)
        self.assertIs(kwargs["asset"], schedule.asset)
        self.assertIs(kwargs["schedule"], schedule)

    @patch("maintenance.workflows.preventive.MaintenanceScheduleCycle.objects.get_or_create")
    def test_unassigned_asset_cannot_create_work_order(self, get_cycle):
        schedule = self.make_schedule()

        with self.assertRaisesMessage(
            ValidationError,
            "This asset is not assigned to a room or maintenance location.",
        ):
            create_preventive_work_order(schedule, self.user)


class MaintenanceDirectMaterialQuantityTests(SimpleTestCase):
    def test_received_direct_material_is_not_costed_as_used_until_recorded(self):
        material = MaintenanceMaterial(
            source="DIRECT",
            quantity=Decimal("0.00"),
            received_quantity=Decimal("2.00"),
            unit_cost=Decimal("2000.00"),
        )

        self.assertEqual(material.remaining_quantity, Decimal("2.00"))
        self.assertEqual(material.received_cost, Decimal("4000.00"))
        self.assertEqual(material.total_cost, Decimal("0.00"))

    def test_partial_usage_reduces_remaining_quantity_and_costs_used_amount(self):
        material = MaintenanceMaterial(
            source="DIRECT",
            quantity=Decimal("1.00"),
            received_quantity=Decimal("2.00"),
            unit_cost=Decimal("2000.00"),
        )

        self.assertEqual(material.remaining_quantity, Decimal("1.00"))
        self.assertEqual(material.total_cost, Decimal("2000.00"))

    def test_fully_used_direct_material_has_no_remaining_quantity(self):
        material = MaintenanceMaterial(
            source="DIRECT",
            quantity=Decimal("2.00"),
            received_quantity=Decimal("2.00"),
            unit_cost=Decimal("2000.00"),
        )

        self.assertEqual(material.remaining_quantity, Decimal("0.00"))
        self.assertEqual(material.total_cost, Decimal("4000.00"))
