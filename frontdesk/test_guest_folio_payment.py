from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from billing.models import Charge, Folio, Guest
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
