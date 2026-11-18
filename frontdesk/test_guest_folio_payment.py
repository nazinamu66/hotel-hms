from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from billing.models import Charge, Folio, FolioBalanceWaiver, FolioCheckoutAuthorization, Guest, Reservation
from rooms.models import Room, RoomCategory
from inventory.models import Department, Hotel, Organization


class GuestFolioPaymentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Guest Folio Payment Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Guest Folio Payment Hotel",
        )
        cls.frontdesk = Department.objects.create(
            hotel=cls.hotel,
            code="GFP",
            name="Guest Folio Payment Frontdesk",
            department_type="FRONTDESK",
        )
        cls.user = User.objects.create_user(
            username="guest_folio_payment_user",
            password="test-password",
            role="FRONTDESK",
            hotel=cls.hotel,
            department=cls.frontdesk,
        )
        cls.guest = Guest.objects.create(
            hotel=cls.hotel,
            first_name="Guest",
            last_name="Payment",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            guest=self.guest,
        )
        self.charge = Charge.objects.create(
            folio=self.folio,
            description="Room charge",
            department=self.frontdesk,
            amount=Decimal("2500.00"),
            reference="GFP-ROOM-CHARGE",
        )

    def test_guest_profile_shows_folio_balance_and_receive_payment_action(self):
        response = self.client.get(
            reverse("frontdesk_guest_profile", args=[self.guest.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outstanding")
        self.assertContains(response, "₦2500.00")
        self.assertContains(
            response,
            reverse("frontdesk_folio_take_payment", args=[self.folio.pk]),
        )

    def test_selected_folio_payment_page_displays_correct_folio(self):
        response = self.client.get(
            reverse("frontdesk_folio_take_payment", args=[self.folio.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["folio"].pk, self.folio.pk)
        self.assertEqual(response.context["balance"], Decimal("2500.00"))
        self.assertContains(response, f"#{self.folio.pk}")
        self.assertContains(response, "Outstanding Balance")

    @patch("frontdesk.workflows.payment.take_payment_for_folio")
    def test_post_uses_the_selected_folio_and_returns_to_guest_profile(self, process_payment):
        response = self.client.post(
            reverse("frontdesk_folio_take_payment", args=[self.folio.pk]),
            {
                "amount": "1000.00",
                "method": "CASH",
                "reference": "",
                "note": "Part payment",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("frontdesk_guest_profile", args=[self.guest.pk]),
        )
        process_payment.assert_called_once()
        self.assertEqual(
            process_payment.call_args.kwargs["folio"].pk,
            self.folio.pk,
        )
        self.assertEqual(
            process_payment.call_args.kwargs["amount"],
            Decimal("1000.00"),
        )

    def test_closed_folio_cannot_receive_payment(self):
        self.folio.is_closed = True
        self.folio.save(update_fields=["is_closed"])

        with patch("frontdesk.workflows.payment.take_payment_for_folio") as process_payment:
            response = self.client.get(
                reverse("frontdesk_folio_take_payment", args=[self.folio.pk])
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("frontdesk_guest_profile", args=[self.guest.pk]),
        )
        process_payment.assert_not_called()

    def test_folio_from_another_hotel_is_not_accessible(self):
        other_org = Organization.objects.create(name="Other Guest Folio Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Guest Folio Hotel",
        )
        other_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Other",
            last_name="Guest",
        )
        other_folio = Folio.objects.create(
            hotel=other_hotel,
            folio_type="ROOM",
            guest=other_guest,
        )

        response = self.client.get(
            reverse("frontdesk_folio_take_payment", args=[other_folio.pk])
        )

        self.assertEqual(response.status_code, 404)


    def test_invoice_pdf_endpoint_returns_a_real_pdf_attachment(self):
        response = self.client.get(
            reverse("frontdesk_invoice_pdf", args=[self.folio.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_invoice_page_is_scoped_to_the_users_hotel(self):
        other_org = Organization.objects.create(name="Other Invoice Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Invoice Hotel",
        )
        other_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Other",
            last_name="Invoice Guest",
        )
        other_folio = Folio.objects.create(
            hotel=other_hotel,
            folio_type="ROOM",
            guest=other_guest,
        )

        response = self.client.get(
            reverse("frontdesk_invoice", args=[other_folio.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_invoice_pdf_is_scoped_to_the_users_hotel(self):
        other_org = Organization.objects.create(name="Other PDF Invoice Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other PDF Invoice Hotel",
        )
        other_guest = Guest.objects.create(
            hotel=other_hotel,
            first_name="Other",
            last_name="PDF Guest",
        )
        other_folio = Folio.objects.create(
            hotel=other_hotel,
            folio_type="ROOM",
            guest=other_guest,
        )

        response = self.client.get(
            reverse("frontdesk_invoice_pdf", args=[other_folio.pk])
        )

        self.assertEqual(response.status_code, 404)


    def _create_active_stay_room(self, guest=None):
        category, _ = RoomCategory.objects.get_or_create(
            name="Guest Folio Payment Test Category",
        )
        room = Room.objects.create(
            hotel=self.hotel,
            room_number="GFP-01",
            category=category,
            status="OCCUPIED",
        )
        folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="ROOM",
            room=room,
            guest=guest,
        )
        return room, folio

    def test_active_stay_handles_missing_guest_record(self):
        room, folio = self._create_active_stay_room(guest=None)

        response = self.client.get(
            reverse("frontdesk_active_stay", args=[room.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["folio"].pk, folio.pk)
        self.assertContains(response, "Guest information missing")
        self.assertContains(response, "Guest details have not been recorded")

    @patch("frontdesk.workflows.checkout.checkout_guest")
    def test_checkout_rejects_get_without_running_checkout_workflow(self, checkout_guest):
        room, _folio = self._create_active_stay_room(guest=self.guest)

        response = self.client.get(
            reverse("frontdesk_checkout", args=[room.pk])
        )

        self.assertEqual(response.status_code, 405)
        checkout_guest.assert_not_called()

    @patch("frontdesk.workflows.checkout.checkout_guest")
    def test_checkout_runs_only_after_post(self, checkout_guest):
        room, folio = self._create_active_stay_room(guest=self.guest)

        response = self.client.post(
            reverse("frontdesk_checkout", args=[room.pk])
        )

        self.assertEqual(response.status_code, 302)
        checkout_guest.assert_called_once_with(room=room, user=self.user, reason="")


    def test_frontdesk_cannot_checkout_with_an_outstanding_balance(self):
        room, folio = self._create_active_stay_room(guest=self.guest)
        Charge.objects.create(
            folio=folio, description="Outstanding room charge",
            department=self.frontdesk, amount=Decimal("2500.00"),
            reference="GFP-CHECKOUT-BALANCE",
        )

        from frontdesk.workflows.checkout import checkout_guest
        with self.assertRaises(ValidationError):
            checkout_guest(room, self.user)

        folio.refresh_from_db()
        self.assertFalse(folio.is_closed)
        self.assertEqual(folio.balance, Decimal("2500.00"))
        self.assertEqual(FolioCheckoutAuthorization.objects.filter(folio=folio).count(), 0)

    def test_manager_must_give_reason_and_checkout_keeps_guest_debt(self):
        room, folio = self._create_active_stay_room(guest=self.guest)
        Charge.objects.create(
            folio=folio, description="Outstanding room charge",
            department=self.frontdesk, amount=Decimal("2500.00"),
            reference="GFP-MANAGER-CHECKOUT",
        )
        User = get_user_model()
        manager = User.objects.create_user(
            username="guest_folio_manager", password="test-password",
            role="MANAGER", hotel=self.hotel, department=self.frontdesk,
        )
        from frontdesk.workflows.checkout import checkout_guest

        with self.assertRaises(ValidationError):
            checkout_guest(room, manager)

        checkout_guest(room, manager, reason="Guest departure approved; debt remains collectible")
        folio.refresh_from_db()
        authorization = FolioCheckoutAuthorization.objects.get(folio=folio)
        self.assertTrue(folio.is_closed)
        self.assertEqual(folio.balance, Decimal("2500.00"))
        self.assertEqual(authorization.approved_by, manager)
        self.assertEqual(authorization.outstanding_balance, Decimal("2500.00"))
        self.assertEqual(authorization.reason, "Guest departure approved; debt remains collectible")

    def test_director_can_partially_and_fully_waive_balance_with_journals(self):
        room, folio = self._create_active_stay_room(guest=self.guest)
        Charge.objects.create(
            folio=folio, description="Outstanding room charge",
            department=self.frontdesk, amount=Decimal("2500.00"),
            reference="GFP-DIRECTOR-WAIVER",
        )
        User = get_user_model()
        director = User.objects.create_user(
            username="guest_folio_director", password="test-password",
            role="DIRECTOR", organization=self.hotel.organization,
        )
        from billing.services.folio_waiver import waive_folio_balance
        from accounting.models import JournalEntry

        partial = waive_folio_balance(
            folio_id=folio.pk, amount="1000.00",
            reason="Approved partial settlement", user=director,
        )
        self.assertEqual(partial.amount, Decimal("1000.00"))
        folio.refresh_from_db()
        self.assertEqual(folio.balance, Decimal("1500.00"))
        self.assertTrue(
            JournalEntry.objects.filter(
                hotel=self.hotel, reference=partial.journal_reference,
            ).exists()
        )

        full = waive_folio_balance(
            folio_id=folio.pk, amount="1500.00",
            reason="Final balance approved for write-off", user=director,
        )
        folio.refresh_from_db()
        self.assertEqual(folio.balance, Decimal("0.00"))
        self.assertEqual(FolioBalanceWaiver.objects.filter(folio=folio).count(), 2)
        self.assertTrue(
            JournalEntry.objects.filter(
                hotel=self.hotel, reference=full.journal_reference,
            ).exists()
        )

    def test_manager_cannot_waive_folio_balance(self):
        room, folio = self._create_active_stay_room(guest=self.guest)
        Charge.objects.create(
            folio=folio, description="Outstanding room charge",
            department=self.frontdesk, amount=Decimal("2500.00"),
            reference="GFP-MANAGER-WAIVER",
        )
        User = get_user_model()
        manager = User.objects.create_user(
            username="guest_folio_manager_waiver", password="test-password",
            role="MANAGER", hotel=self.hotel, department=self.frontdesk,
        )
        from billing.services.folio_waiver import waive_folio_balance

        with self.assertRaises(ValidationError):
            waive_folio_balance(
                folio_id=folio.pk, amount="500.00",
                reason="Not authorised", user=manager,
            )

    def test_reservation_calendar_reuses_category_day_availability(self):
        category, _ = RoomCategory.objects.get_or_create(
            name="Calendar Performance Test Category",
        )
        Room.objects.create(
            hotel=self.hotel,
            room_number="CAL-01",
            category=category,
            status="AVAILABLE",
        )
        Room.objects.create(
            hotel=self.hotel,
            room_number="CAL-02",
            category=category,
            status="AVAILABLE",
        )

        with patch("frontdesk.views.get_available_rooms", wraps=__import__(
            "frontdesk.services", fromlist=["get_available_rooms"]
        ).get_available_rooms) as availability_lookup:
            response = self.client.get(
                reverse("frontdesk_reservation_calendar"),
                {"start": "2026-11-17"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["days"]), 14)
        self.assertEqual(len(response.context["calendar"]), 2)
        # Two rooms share one category, so availability is checked once
        # per date rather than once per room/date cell.
        self.assertEqual(availability_lookup.call_count, 14)

    def test_reservation_calendar_ignores_invalid_category_filter(self):
        response = self.client.get(
            reverse("frontdesk_reservation_calendar"),
            {"category": "not-a-category", "room": "NO-MATCH"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["calendar"], [])
        self.assertEqual(response.context["filters"]["category"], "")
        self.assertEqual(response.context["filters"]["room"], "NO-MATCH")


    def test_arrivals_board_uses_local_date_after_utc_midnight_boundary(self):
        category, _ = RoomCategory.objects.get_or_create(
            name="Local Date Arrivals Test Category",
        )
        reservation = Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=category,
            check_in_date=date(2026, 11, 17),
            check_out_date=date(2026, 11, 18),
            status="RESERVED",
            created_by=self.user,
        )

        # At 00:15 in UTC+1 it is still 23:15 UTC on the previous day.
        utc_now = datetime(2026, 11, 16, 23, 15, tzinfo=dt_timezone.utc)
        with patch("frontdesk.views.timezone.now", return_value=utc_now), patch(
            "frontdesk.views.timezone.localdate",
            return_value=date(2026, 11, 17),
        ):
            response = self.client.get(reverse("frontdesk_today_board"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["arrivals"].values_list("id", flat=True)),
            [reservation.id],
        )
        self.assertContains(response, "Expected arrivals")
        self.assertContains(response, "Guest")
