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
            code="TST-4000",
            name="Room Revenue",
            account_type="income",
        )
        self.cogs = Account.objects.create(
            hotel=self.hotel,
            code="TST-5000",
            name="Cost of Goods Sold",
            account_type="cogs",
        )
        self.expense = Account.objects.create(
            hotel=self.hotel,
            code="TST-6000",
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


    def test_financial_position_uses_ledger_and_deposit_subledgers(self):
        from billing.models import Company, CompanyDeposit, Guest, GuestDeposit

        # Hotel setup creates these system accounts automatically.
        # Reuse them so the test respects the per-hotel unique slug constraint.
        cash = Account.objects.get(hotel=self.hotel, system_key="cash")
        receivable = Account.objects.get(
            hotel=self.hotel,
            system_key="accounts_receivable",
        )
        payable = Account.objects.get(
            hotel=self.hotel,
            system_key="accounts_payable",
        )
        bank = Account.objects.create(
            hotel=self.hotel,
            code="TST-1011",
            name="Main Bank",
            account_type="bank",
        )
        position_entry = JournalEntry.objects.create(
            hotel=self.hotel,
            date=date(2026, 11, 10),
            description="Financial position test",
            reference="DASHBOARD-POSITION-1",
        )
        for account, debit, credit in (
            (cash, "500.00", "0.00"),
            (bank, "1200.00", "0.00"),
            (receivable, "800.00", "0.00"),
            (payable, "0.00", "300.00"),
        ):
            JournalLine.objects.create(
                journal=position_entry,
                account=account,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )

        company = Company.objects.create(
            hotel=self.hotel,
            name="Dashboard Corporate Client",
        )
        CompanyDeposit.objects.create(
            hotel=self.hotel,
            company=company,
            balance=Decimal("450.00"),
        )
        guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Dashboard",
            last_name="Guest",
        )
        GuestDeposit.objects.create(
            hotel=self.hotel,
            guest=guest,
            balance=Decimal("150.00"),
        )

        dashboard = get_director_dashboard(
            Hotel.objects.filter(organization=self.organization),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )

        self.assertEqual(dashboard["cash_balance"], Decimal("500.00"))
        self.assertEqual(dashboard["bank_balance"], Decimal("1200.00"))
        self.assertEqual(dashboard["accounts_receivable"], Decimal("800.00"))
        self.assertEqual(dashboard["accounts_payable"], Decimal("300.00"))
        self.assertEqual(dashboard["corporate_deposits_held"], Decimal("450.00"))
        self.assertEqual(dashboard["guest_deposits_held"], Decimal("150.00"))

    def test_financial_position_respects_selected_hotel(self):
        cash = Account.objects.create(
            hotel=self.other_hotel,
            code="TST-1000",
            name="Other Hotel Cash",
            account_type="cash",
            system_key="cash",
            is_system=True,
        )
        entry = JournalEntry.objects.create(
            hotel=self.other_hotel,
            date=date(2026, 11, 10),
            description="Other hotel cash",
            reference="DASHBOARD-POSITION-OTHER",
        )
        JournalLine.objects.create(
            journal=entry,
            account=cash,
            debit=Decimal("999.00"),
        )

        dashboard = get_director_dashboard(
            Hotel.objects.filter(organization=self.organization),
            selected_hotel=self.hotel,
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )

        self.assertEqual(dashboard["cash_balance"], Decimal("0.00"))


from core.services.management_reporting import (
    build_management_reporting_context,
    get_management_reporting_scope,
    parse_management_date_range,
)


class ManagementReportingFoundationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Management Reporting Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Management Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Management Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Management Hotel",
        )
        self.inactive_hotel = Hotel.objects.create(
            organization=self.organization,
            name="Inactive Management Hotel",
            is_active=False,
        )

    def test_director_scope_contains_only_active_organization_hotels(self):
        director = User.objects.create_user(
            username="reporting-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )

        scope = get_management_reporting_scope(director)

        self.assertQuerySetEqual(
            scope["hotels"],
            [self.hotel_one, self.hotel_two],
            transform=lambda hotel: hotel,
        )
        self.assertNotIn(self.outside_hotel, scope["hotels"])
        self.assertNotIn(self.inactive_hotel, scope["hotels"])

    def test_general_manager_scope_contains_only_assigned_active_hotels(self):
        general_manager = User.objects.create_user(
            username="reporting-general-manager",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(
            self.hotel_two,
            self.inactive_hotel,
        )

        scope = get_management_reporting_scope(general_manager)

        self.assertQuerySetEqual(
            scope["hotels"],
            [self.hotel_two],
            transform=lambda hotel: hotel,
        )
        self.assertNotIn(self.hotel_one, scope["hotels"])
        self.assertNotIn(self.inactive_hotel, scope["hotels"])

    def test_selected_hotel_must_belong_to_accessible_scope(self):
        director = User.objects.create_user(
            username="reporting-selection-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )

        allowed = get_management_reporting_scope(
            director,
            hotel_id=str(self.hotel_two.pk),
        )
        denied = get_management_reporting_scope(
            director,
            hotel_id=str(self.outside_hotel.pk),
        )

        self.assertEqual(allowed["selected_hotel"], self.hotel_two)
        self.assertQuerySetEqual(
            allowed["scoped_hotels"],
            [self.hotel_two],
            transform=lambda hotel: hotel,
        )
        self.assertIsNone(denied["selected_hotel"])
        self.assertQuerySetEqual(
            denied["scoped_hotels"],
            [self.hotel_one, self.hotel_two],
            transform=lambda hotel: hotel,
        )

    def test_date_range_defaults_to_current_month(self):
        date_from, date_to = parse_management_date_range(
            {},
            today=date(2026, 11, 19),
        )
        self.assertEqual(date_from, date(2026, 11, 1))
        self.assertEqual(date_to, date(2026, 11, 19))

    def test_invalid_or_reversed_dates_fall_back_to_current_month(self):
        expected = (date(2026, 11, 1), date(2026, 11, 19))
        self.assertEqual(
            parse_management_date_range(
                {"from": "not-a-date", "to": "2026-11-19"},
                today=date(2026, 11, 19),
            ),
            expected,
        )
        self.assertEqual(
            parse_management_date_range(
                {"from": "2026-11-20", "to": "2026-11-19"},
                today=date(2026, 11, 19),
            ),
            expected,
        )

    def test_context_combines_scope_and_date_filters(self):
        general_manager = User.objects.create_user(
            username="reporting-context-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_one)

        context = build_management_reporting_context(
            general_manager,
            {
                "hotel": str(self.hotel_one.pk),
                "from": "2026-10-01",
                "to": "2026-10-31",
            },
        )

        self.assertEqual(context["selected_hotel"], self.hotel_one)
        self.assertEqual(context["date_from"], date(2026, 10, 1))
        self.assertEqual(context["date_to"], date(2026, 10, 31))
        self.assertQuerySetEqual(
            context["scoped_hotels"],
            [self.hotel_one],
            transform=lambda hotel: hotel,
        )


class ManagementOverviewViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Management Overview Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Overview Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Overview Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Overview Hotel",
        )

    def test_director_can_view_group_and_select_an_accessible_hotel(self):
        director = User.objects.create_user(
            username="overview-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get("/management/overview/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["hotel"].pk for row in response.context["hotel_rows"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )

        response = self.client.get(
            "/management/overview/",
            {"hotel": str(self.hotel_two.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["hotel"].pk for row in response.context["hotel_rows"]],
            [self.hotel_two.pk],
        )

    def test_general_manager_is_limited_to_assigned_active_hotels(self):
        general_manager = User.objects.create_user(
            username="overview-general-manager",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(general_manager)

        response = self.client.get("/management/overview/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["hotel"].pk for row in response.context["hotel_rows"]},
            {self.hotel_two.pk},
        )
        self.assertEqual(
            [hotel.pk for hotel in response.context["hotels"]],
            [self.hotel_two.pk],
        )

    def test_inaccessible_hotel_filter_never_exposes_outside_data(self):
        director = User.objects.create_user(
            username="overview-isolation-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get(
            "/management/overview/",
            {"hotel": str(self.outside_hotel.pk)},
        )
        self.assertEqual(response.status_code, 200)
        hotel_ids = {row["hotel"].pk for row in response.context["hotel_rows"]}
        self.assertNotIn(self.outside_hotel.pk, hotel_ids)
        self.assertEqual(hotel_ids, {self.hotel_one.pk, self.hotel_two.pk})

    def test_operational_user_cannot_open_management_overview(self):
        frontdesk_user = User.objects.create_user(
            username="overview-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(frontdesk_user)

        response = self.client.get("/management/overview/")
        self.assertEqual(response.status_code, 403)


class ManagementHotelPerformanceViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Hotel Performance Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Performance Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Performance Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(name="Outside Performance Hotel")

    def test_director_can_view_ranked_hotels_in_organization(self):
        director = User.objects.create_user(
            username="performance-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get("/management/hotels/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["hotel"].pk for row in response.context["hotel_rows"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
        self.assertEqual(
            [row["performance_rank"] for row in response.context["hotel_rows"]],
            [1, 2],
        )

    def test_general_manager_sees_only_assigned_hotels(self):
        general_manager = User.objects.create_user(
            username="performance-general-manager",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(general_manager)

        response = self.client.get("/management/hotels/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["hotel"].pk for row in response.context["hotel_rows"]],
            [self.hotel_two.pk],
        )

    def test_operational_user_cannot_view_hotel_performance(self):
        frontdesk_user = User.objects.create_user(
            username="performance-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(frontdesk_user)

        response = self.client.get("/management/hotels/")

        self.assertEqual(response.status_code, 403)

    def test_selected_hotel_filter_cannot_expose_outside_hotel(self):
        director = User.objects.create_user(
            username="performance-isolation-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get(
            "/management/hotels/",
            {"hotel": str(self.outside_hotel.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["hotel"].pk for row in response.context["hotel_rows"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
