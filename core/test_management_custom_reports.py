from django.test import SimpleTestCase, TestCase
from django.http import QueryDict

from accounts.models import User
from inventory.models import Hotel, Organization
from core.services.management_report_configuration import (
    build_management_report_configuration,
)


class ManagementReportConfigurationTests(SimpleTestCase):
    def test_breakdowns_are_limited_to_intersection_of_selected_metrics(self):
        params = QueryDict(
            "metric=revenue&metric=actual_room_nights"
            "&breakdown=hotel&breakdown=date&breakdown=department"
        )

        config = build_management_report_configuration(params)

        self.assertEqual(
            config["selected_metric_ids"],
            ["revenue", "actual_room_nights"],
        )
        self.assertEqual(
            [item["id"] for item in config["breakdown_options"]],
            ["hotel"],
        )
        self.assertEqual(config["selected_breakdowns"], ["hotel"])

    def test_unknown_metric_and_unsupported_breakdown_are_ignored(self):
        params = QueryDict(
            "metric=not_a_real_metric&breakdown=department"
        )

        config = build_management_report_configuration(params)

        self.assertEqual(config["selected_metric_ids"], [])
        self.assertEqual(config["selected_breakdowns"], [])
        self.assertTrue(config["has_invalid_metric_ids"])
        self.assertFalse(config["has_metric_selection"])

    def test_one_metric_exposes_only_its_declared_breakdowns(self):
        params = QueryDict("metric=corporate_revenue&breakdown=company&breakdown=guest")

        config = build_management_report_configuration(params)

        self.assertEqual(
            [item["id"] for item in config["breakdown_options"]],
            ["hotel", "company"],
        )
        self.assertEqual(config["selected_breakdowns"], ["company"])


class ManagementCustomReportsViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Custom Reports Organization"
        )
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Custom Reports Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Custom Reports Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Custom Reports Hotel"
        )

    def test_director_can_open_custom_reports_with_organization_scope(self):
        user = User.objects.create_user(
            username="custom-reports-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(user)

        response = self.client.get("/management/custom-reports/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Reports")
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )

    def test_general_manager_is_limited_to_assigned_hotels(self):
        user = User.objects.create_user(
            username="custom-reports-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        user.assigned_hotels.add(self.hotel_two)
        self.client.force_login(user)

        response = self.client.get("/management/custom-reports/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [hotel.pk for hotel in response.context["hotels"]],
            [self.hotel_two.pk],
        )

    def test_inaccessible_hotel_filter_does_not_expand_scope(self):
        user = User.objects.create_user(
            username="custom-reports-scope",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(user)

        response = self.client.get(
            "/management/custom-reports/",
            {"hotel": str(self.outside_hotel.pk), "metric": "revenue"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_hotel"])
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )

    def test_operational_user_cannot_open_custom_reports(self):
        user = User.objects.create_user(
            username="custom-reports-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(user)

        response = self.client.get("/management/custom-reports/")

        self.assertEqual(response.status_code, 403)
