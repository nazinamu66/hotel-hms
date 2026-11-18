from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from billing.models import Charge, Folio, Guest
from frontdesk.workflows.checkout import checkout_guest
from frontdesk.workflows.extend_stay import extend_guest_stay
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory


class FrontdeskCheckoutWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Checkout Workflow Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Checkout Workflow Hotel",
        )
        cls.frontdesk = Department.objects.create(
            hotel=cls.hotel,
            code="CWF",
            name="Checkout Workflow Frontdesk",
            department_type="FRONTDESK",
        )
        cls.frontdesk_user = User.objects.create_user(
            username="checkout_frontdesk_user",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )
        cls.manager = User.objects.create_user(
            username="checkout_manager_user",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Checkout",
            last_name="Guest",
        )
        cls.category = RoomCategory.objects.create(name="Checkout Workflow Category")

    def setUp(self):
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_number="CWF-01",
            category=self.category,
            status="OCCUPIED",
        )
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            room=self.room,
            guest=self.guest,
        )

    def make_charge(self, *, billing_party="INDIVIDUAL", amount="25000.00"):
        return Charge.objects.create(
            folio=self.folio,
            description="Room night",
            department=self.frontdesk,
            amount=Decimal(amount),
            billing_party=billing_party,
            reference=f"TEST-{billing_party}",
        )

    def test_checkout_does_not_create_an_additional_room_charge(self):
        company_charge = self.make_charge(billing_party="COMPANY")

        checked_out = checkout_guest(self.room, self.frontdesk_user)

        self.folio.refresh_from_db()
        self.room.refresh_from_db()
        self.assertTrue(checked_out.is_closed)
        self.assertTrue(self.folio.is_closed)
        self.assertIsNotNone(self.folio.check_out_at)
        self.assertEqual(self.room.status, "VACANT_DIRTY")
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 1)
        self.assertEqual(self.folio.total_company_charges, Decimal("25000.00"))
        self.assertEqual(self.folio.balance, Decimal("0.00"))
        self.assertEqual(company_charge.pk, self.folio.charges.get().pk)

    def test_frontdesk_cannot_checkout_with_unpaid_individual_balance(self):
        self.make_charge()

        with self.assertRaisesMessage(
            ValidationError,
            "Outstanding balance. Manager, Director or Admin approval required.",
        ):
            checkout_guest(self.room, self.frontdesk_user)

        self.folio.refresh_from_db()
        self.room.refresh_from_db()
        self.assertFalse(self.folio.is_closed)
        self.assertIsNone(self.folio.check_out_at)
        self.assertEqual(self.room.status, "OCCUPIED")
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 1)

    def test_manager_can_authorise_checkout_with_outstanding_balance(self):
        self.make_charge()

        checkout_guest(self.room, self.manager, reason="Approved guest departure; debt remains due.")

        self.folio.refresh_from_db()
        self.room.refresh_from_db()
        self.assertTrue(self.folio.is_closed)
        self.assertIsNotNone(self.folio.check_out_at)
        self.assertEqual(self.room.status, "VACANT_DIRTY")
        self.assertEqual(Charge.objects.filter(folio=self.folio).count(), 1)


class DailyRoomChargeCheckoutBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Daily Charge Boundary Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Daily Charge Boundary Hotel",
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Daily",
            last_name="Guest",
        )
        cls.category = RoomCategory.objects.create(name="Daily Charge Boundary Category")

    def setUp(self):
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_number="DCB-01",
            category=self.category,
            status="OCCUPIED",
        )
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            room=self.room,
            guest=self.guest,
            last_room_charge_date=date(2026, 11, 19),
            check_out_at=timezone.make_aware(datetime(2026, 11, 20, 12, 0)),
        )

    @patch("billing.services.room_pricing.create_room_night_charge")
    def test_night_audit_does_not_charge_the_checkout_date(self, create_charge):
        audit_time = timezone.make_aware(datetime(2026, 11, 20, 2, 0))

        with patch("billing.models.timezone.now", return_value=audit_time), patch(
            "billing.models.timezone.localdate",
            side_effect=lambda value=None: (
                date(2026, 11, 20) if value is None else value.date()
            ),
        ):
            self.folio.apply_daily_room_charge()

        create_charge.assert_not_called()
        self.folio.refresh_from_db()
        self.assertEqual(self.folio.last_room_charge_date, date(2026, 11, 19))

    @patch("billing.services.room_pricing.create_room_night_charge")
    def test_night_audit_charges_the_night_before_checkout(self, create_charge):
        self.folio.last_room_charge_date = date(2026, 11, 18)
        self.folio.save(update_fields=["last_room_charge_date"])
        audit_time = timezone.make_aware(datetime(2026, 11, 19, 2, 0))

        with patch("billing.models.timezone.now", return_value=audit_time), patch(
            "billing.models.timezone.localdate",
            side_effect=lambda value=None: (
                date(2026, 11, 19) if value is None else value.date()
            ),
        ):
            self.folio.apply_daily_room_charge()

        create_charge.assert_called_once()
        self.assertEqual(
            create_charge.call_args.kwargs["charge_date"],
            date(2026, 11, 19),
        )
        self.folio.refresh_from_db()
        self.assertEqual(self.folio.last_room_charge_date, date(2026, 11, 19))


class StayExtensionRoomChargeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Stay Extension Charge Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Stay Extension Charge Hotel",
        )
        cls.frontdesk = Department.objects.create(
            hotel=cls.hotel,
            code="STAYEXT",
            name="Stay Extension Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="stay_extension_user",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Extension",
            last_name="Guest",
        )
        cls.category = RoomCategory.objects.create(name="Stay Extension Category")

    def setUp(self):
        self.room = Room.objects.create(
            hotel=self.hotel,
            room_number="SEC-01",
            category=self.category,
            status="OCCUPIED",
        )
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            room=self.room,
            guest=self.guest,
            last_room_charge_date=date(2026, 11, 19),
            check_out_at=timezone.make_aware(datetime(2026, 11, 20, 12, 0)),
        )

    @patch("billing.services.room_pricing.create_room_night_charge")
    def test_extension_posts_each_added_night_and_advances_charge_cursor(self, create_charge):
        result = extend_guest_stay(
            room=self.room,
            new_checkout="2026-11-22",
            charged_by=self.user,
        )

        self.assertEqual(result.pk, self.folio.pk)
        self.assertEqual(create_charge.call_count, 2)
        self.assertEqual(
            [call.kwargs["charge_date"] for call in create_charge.call_args_list],
            [date(2026, 11, 20), date(2026, 11, 21)],
        )
        self.folio.refresh_from_db()
        self.assertEqual(self.folio.last_room_charge_date, date(2026, 11, 21))
        self.assertEqual(
            timezone.localdate(self.folio.check_out_at),
            date(2026, 11, 22),
        )

        # The immediate extension charges must not be repeated by the next
        # Night Audit run while the stay remains in progress.
        audit_time = timezone.make_aware(datetime(2026, 11, 20, 2, 0))
        with patch("billing.models.timezone.now", return_value=audit_time), patch(
            "billing.models.timezone.localdate",
            side_effect=lambda value=None: (
                date(2026, 11, 20) if value is None else value.date()
            ),
        ):
            self.folio.apply_daily_room_charge()
        self.assertEqual(create_charge.call_count, 2)
