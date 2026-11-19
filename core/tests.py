from django.contrib.auth import get_user_model
from django.test import TestCase

from core.utils import get_user_hotels
from inventory.models import Hotel, Organization


User = get_user_model()


class GetUserHotelsTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Dashboard Test Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Dashboard Test Hotel",
        )
        self.other_organization = Organization.objects.create(
            name="Other Dashboard Org"
        )
        self.other_hotel = Hotel.objects.create(
            organization=self.other_organization,
            name="Other Dashboard Hotel",
        )

    def test_admin_sees_hotels_in_assigned_organization(self):
        admin = User.objects.create_user(
            username="org-admin",
            password="test-password",
            role="ADMIN",
            organization=self.organization,
        )

        self.assertQuerySetEqual(
            get_user_hotels(admin),
            [self.hotel],
        )

    def test_admin_without_organization_sees_no_hotels(self):
        admin = User.objects.create_user(
            username="unassigned-admin",
            password="test-password",
            role="ADMIN",
        )

        self.assertFalse(get_user_hotels(admin).exists())

    def test_director_is_scoped_to_their_organization(self):
        director = User.objects.create_user(
            username="org-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )

        self.assertQuerySetEqual(
            get_user_hotels(director),
            [self.hotel],
        )

    def test_hotel_user_sees_only_their_assigned_hotel(self):
        manager = User.objects.create_user(
            username="hotel-manager",
            password="test-password",
            role="MANAGER",
            hotel=self.hotel,
        )

        self.assertQuerySetEqual(
            get_user_hotels(manager),
            [self.hotel],
        )


from datetime import date
from decimal import Decimal

from accounting.models import Account, JournalEntry, JournalLine
from core.services.dashboard.director_dashboard import get_director_dashboard


class DirectorDashboardTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Director Dashboard Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Dashboard Hotel One",
        )
        self.other_hotel = Hotel.objects.create(
            organization=self.organization,
            name="Dashboard Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Organization Hotel",
        )
        self.income = Account.objects.create(
            hotel=self.hotel,
            code="4000",
            name="Room Revenue",
            account_type="income",
        )
        self.cogs = Account.objects.create(
            hotel=self.hotel,
            code="5000",
            name="Cost of Goods Sold",
            account_type="cogs",
        )
        self.expense = Account.objects.create(
            hotel=self.hotel,
            code="6000",
            name="Operating Expense",
            account_type="expense",
        )
        entry = JournalEntry.objects.create(
            hotel=self.hotel,
            date=date(2026, 11, 10),
            description="Dashboard test posting",
            reference="DASHBOARD-TEST-1",
        )
        JournalLine.objects.create(
            journal=entry,
            account=self.income,
            credit=Decimal("1000.00"),
        )
        JournalLine.objects.create(
            journal=entry,
            account=self.cogs,
            debit=Decimal("250.00"),
        )
        JournalLine.objects.create(
            journal=entry,
            account=self.expense,
            debit=Decimal("100.00"),
        )

    def test_director_metrics_use_posted_ledger_entries(self):
        dashboard = get_director_dashboard(
            Hotel.objects.filter(organization=self.organization),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )
        self.assertEqual(dashboard["revenue"], Decimal("1000.00"))
        self.assertEqual(dashboard["cogs"], Decimal("250.00"))
        self.assertEqual(dashboard["expenses"], Decimal("100.00"))
        self.assertEqual(dashboard["operating_result"], Decimal("650.00"))
        self.assertFalse(dashboard["trend_empty"])
        self.assertIn("<polyline", dashboard["trend_svg"])
        self.assertEqual(dashboard["hotel_chart_rows"][0]["revenue_bar_width"], 100.0)

    def test_selected_hotel_limits_group_metrics(self):
        dashboard = get_director_dashboard(
            Hotel.objects.filter(organization=self.organization),
            selected_hotel=self.hotel,
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )
        self.assertEqual(len(dashboard["hotel_rows"]), 1)
        self.assertEqual(dashboard["hotel_rows"][0]["hotel"], self.hotel)

    def test_dashboard_does_not_include_outside_organization_hotel(self):
        dashboard = get_director_dashboard(
            Hotel.objects.filter(organization=self.organization),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )
        hotel_ids = {row["hotel"].id for row in dashboard["hotel_rows"]}
        self.assertNotIn(self.outside_hotel.id, hotel_ids)
