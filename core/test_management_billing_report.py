from django.test import TestCase

from accounts.models import User
from inventory.models import Hotel, Organization


class ManagementGuestCompanyBillingViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Billing Report Organization"
        )
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Billing Report Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Billing Report Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Billing Report Hotel"
        )

    def test_director_can_open_billing_report_with_organization_scope(self):
        director = User.objects.create_user(
            username="billing-report-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get("/management/guest-company-billing/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guest &amp; Company Billing")
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
        self.assertNotIn(
            self.outside_hotel,
            response.context["hotels"],
        )

    def test_general_manager_is_limited_to_assigned_hotels(self):
        manager = User.objects.create_user(
            username="billing-report-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(manager)

        response = self.client.get("/management/guest-company-billing/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_two.pk},
        )

    def test_operational_user_cannot_open_billing_report(self):
        user = User.objects.create_user(
            username="billing-report-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(user)

        response = self.client.get("/management/guest-company-billing/")

        self.assertEqual(response.status_code, 403)
