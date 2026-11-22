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

    def test_hotel_row_drilldown_preserves_dates_and_limits_to_selected_hotel(self):
        director = User.objects.create_user(
            username="performance-drilldown-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)

        response = self.client.get(
            "/management/hotels/",
            {"from": "2026-11-01", "to": "2026-11-20"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"/management/hotels/?hotel={self.hotel_one.pk}&amp;from=2026-11-01&amp;to=2026-11-20",
        )

        detail = self.client.get(
            "/management/hotels/",
            {
                "hotel": str(self.hotel_one.pk),
                "from": "2026-11-01",
                "to": "2026-11-20",
            },
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            [row["hotel"].pk for row in detail.context["hotel_rows"]],
            [self.hotel_one.pk],
        )
        self.assertEqual(detail.context["date_from"].isoformat(), "2026-11-01")
        self.assertEqual(detail.context["date_to"].isoformat(), "2026-11-20")
        self.assertContains(detail, "All accessible hotels")

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


class ManagementFrontDeskAnalyticsViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Front Desk Analytics Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Analytics Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Analytics Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(name="Outside Analytics Hotel")

    def test_director_can_view_organization_front_desk_analytics(self):
        director = User.objects.create_user(
            username="frontdesk-analytics-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get("/management/front-desk/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )

    def test_general_manager_is_limited_to_assigned_hotels(self):
        general_manager = User.objects.create_user(
            username="frontdesk-analytics-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(general_manager)
        response = self.client.get("/management/front-desk/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [hotel.pk for hotel in response.context["hotels"]],
            [self.hotel_two.pk],
        )

    def test_operational_frontdesk_user_cannot_open_management_analytics(self):
        frontdesk_user = User.objects.create_user(
            username="frontdesk-analytics-operational",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(frontdesk_user)
        response = self.client.get("/management/front-desk/")
        self.assertEqual(response.status_code, 403)

    def test_inaccessible_hotel_filter_does_not_expand_scope(self):
        director = User.objects.create_user(
            username="frontdesk-analytics-isolation",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            "/management/front-desk/",
            {"hotel": str(self.outside_hotel.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )


    def test_kpi_drilldowns_return_scoped_folio_and_guest_records(self):
        from datetime import timedelta
        from django.utils import timezone
        from billing.models import Folio, Guest

        now = timezone.now()
        today = timezone.localdate()
        current_guest = Guest.objects.create(
            hotel=self.hotel_one, first_name="Current", last_name="Guest"
        )
        repeat_guest = Guest.objects.create(
            hotel=self.hotel_one, first_name="Repeat", last_name="Guest"
        )
        outside_guest = Guest.objects.create(
            hotel=self.outside_hotel, first_name="Outside", last_name="Guest"
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.hotel_one, guest=current_guest,
            check_in_at=now, is_closed=False,
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.hotel_one, guest=repeat_guest,
            check_in_at=now - timedelta(days=30), is_closed=True,
            check_out_at=now - timedelta(days=27),
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.hotel_one, guest=repeat_guest,
            check_in_at=now, is_closed=False,
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.outside_hotel, guest=outside_guest,
            check_in_at=now, is_closed=False,
        )

        director = User.objects.create_user(
            username="frontdesk-drilldown-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        params = {"from": today.isoformat(), "to": today.isoformat()}

        in_house = self.client.get("/management/front-desk/in-house/", params)
        self.assertEqual(in_house.status_code, 200)
        self.assertEqual(in_house.context["record_count"], 2)
        self.assertEqual(
            set(in_house.context["records"].values_list("pk", flat=True)),
            {current_guest.pk, repeat_guest.pk},
        )
        self.assertNotContains(in_house, "Outside Guest")

        repeat = self.client.get("/management/front-desk/repeat-guests/", params)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.context["record_count"], 1)
        self.assertEqual(
            list(repeat.context["records"].values_list("pk", flat=True)),
            [repeat_guest.pk],
        )

        checkins = self.client.get("/management/front-desk/check-ins/", params)
        self.assertEqual(checkins.status_code, 200)
        self.assertEqual(checkins.context["record_count"], 2)
        self.assertNotContains(checkins, "Outside Guest")

    def test_general_manager_drilldown_cannot_expand_assigned_hotel_scope(self):
        from django.utils import timezone
        from billing.models import Folio, Guest

        assigned_guest = Guest.objects.create(
            hotel=self.hotel_two, first_name="Assigned", last_name="Guest"
        )
        outside_guest = Guest.objects.create(
            hotel=self.hotel_one, first_name="Unassigned", last_name="Guest"
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.hotel_two, guest=assigned_guest,
            check_in_at=timezone.now(), is_closed=False,
        )
        Folio.objects.create(
            folio_type="ROOM", hotel=self.hotel_one, guest=outside_guest,
            check_in_at=timezone.now(), is_closed=False,
        )
        manager = User.objects.create_user(
            username="frontdesk-drilldown-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(manager)
        response = self.client.get(
            "/management/front-desk/in-house/",
            {"hotel": str(self.hotel_one.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_count"], 1)
        self.assertEqual(
            list(response.context["records"].values_list("pk", flat=True)),
            [assigned_guest.pk],
        )


class ReservationsOccupancyCalculationTests(TestCase):
    def setUp(self):
        from billing.models import (
            Company,
            GroupReservation,
            GroupReservationRoom,
            Guest,
            Reservation,
            ReservationRoomAllocation,
        )
        from rooms.models import RoomCategory

        self.organization = Organization.objects.create(
            name="Reservations Analytics Org"
        )
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Reservations Analytics Hotel",
        )
        self.other_hotel = Hotel.objects.create(
            name="Outside Reservations Analytics Hotel"
        )
        self.category = RoomCategory.objects.create(
            hotel=self.hotel,
            name="Reservations Analytics Category"
        )
        self.guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Analytics",
            last_name="Guest",
        )
        self.company = Company.objects.create(
            hotel=self.hotel,
            name="Reservations Analytics Company",
        )
        self.Reservation = Reservation
        self.ReservationRoomAllocation = ReservationRoomAllocation
        self.GroupReservation = GroupReservation
        self.GroupReservationRoom = GroupReservationRoom

    def test_counts_legacy_ordinary_and_corporate_room_bookings_once(self):
        from billing.models import Guest
        from core.services.reservations_occupancy import (
            build_reservations_occupancy_analytics,
        )

        legacy = self.Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 11, 25),
            check_out_date=date(2026, 11, 27),
            source="Website",
            status="RESERVED",
        )
        ordinary_parent = self.Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 11, 26),
            check_out_date=date(2026, 11, 28),
            number_of_rooms=1,
            source="Phone",
            status="CHECKED_OUT",
        )
        self.ReservationRoomAllocation.objects.create(
            reservation=ordinary_parent,
            allocation_number=1,
            guest=self.guest,
            check_in_date=date(2026, 11, 26),
            check_out_date=date(2026, 11, 28),
            status="CHECKED_OUT",
        )
        group = self.GroupReservation.objects.create(
            hotel=self.hotel,
            company=self.company,
            reference="GRP-ANALYTICS-1",
            check_in_date=date(2026, 11, 27),
            check_out_date=date(2026, 11, 30),
            status="COMPLETED",
        )
        self.GroupReservationRoom.objects.create(
            group_reservation=group,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 11, 27),
            check_out_date=date(2026, 11, 30),
            status="CHECKED_OUT",
        )

        cancelled = self.Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 11, 28),
            check_out_date=date(2026, 11, 29),
            status="CANCELLED",
        )
        no_show = self.Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 11, 29),
            check_out_date=date(2026, 11, 30),
            status="NO_SHOW",
        )

        result = build_reservations_occupancy_analytics(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )

        # The ordinary parent and its allocation are one room booking;
        # corporate occupants are counted from their group allocation.
        self.assertEqual(result["reservation_count"], 5)
        self.assertEqual(result["arrival_count"], 5)
        self.assertEqual(result["booked_room_nights"], 7)
        self.assertEqual(result["cancelled_count"], 1)
        self.assertEqual(result["no_show_count"], 1)
        self.assertEqual(result["cancellation_rate"], 20.0)
        self.assertEqual(result["no_show_rate"], 20.0)
        self.assertEqual(result["individual_room_bookings"], 4)
        self.assertEqual(result["corporate_room_bookings"], 1)
        self.assertEqual(
            {row["source"] for row in result["booking_source_rows"]},
            {"Website", "Phone", "Not recorded", "Corporate group"},
        )

    def test_room_nights_are_clipped_to_the_selected_reporting_period(self):
        from core.services.reservations_occupancy import (
            build_reservations_occupancy_analytics,
        )

        self.Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=date(2026, 10, 30),
            check_out_date=date(2026, 11, 4),
            status="CHECKED_OUT",
        )

        result = build_reservations_occupancy_analytics(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 2),
        )

        self.assertEqual(result["booked_room_nights"], 2)

    def test_hotel_scope_excludes_other_hotels(self):
        from billing.models import Guest, Reservation
        from rooms.models import RoomCategory
        from core.services.reservations_occupancy import (
            build_reservations_occupancy_analytics,
        )

        other_category = RoomCategory.objects.create(
            hotel=self.other_hotel,
            name="Outside Reservations Category"
        )
        other_guest = Guest.objects.create(
            hotel=self.other_hotel,
            first_name="Outside",
            last_name="Guest",
        )
        Reservation.objects.create(
            hotel=self.other_hotel,
            guest=other_guest,
            room_category=other_category,
            check_in_date=date(2026, 11, 25),
            check_out_date=date(2026, 11, 27),
            status="RESERVED",
        )

        result = build_reservations_occupancy_analytics(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
        )

        self.assertEqual(result["reservation_count"], 0)
        self.assertEqual(result["booked_room_nights"], 0)



class RoomOccupancyAnalyticsTests(TestCase):
    def setUp(self):
        from rooms.models import Room, RoomCategory

        self.organization = Organization.objects.create(
            name="Room Occupancy Analytics Org"
        )
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Room Occupancy Analytics Hotel",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Room Occupancy Hotel"
        )
        self.category = RoomCategory.objects.create(
            hotel=self.hotel,
            name="Room Occupancy Category"
        )
        self.outside_category = RoomCategory.objects.create(
            hotel=self.outside_hotel,
            name="Outside Room Occupancy Category"
        )
        self.guest = __import__("billing.models", fromlist=["Guest"]).Guest.objects.create(
            hotel=self.hotel,
            first_name="Occupancy",
            last_name="Guest",
        )
        self.Room = Room

    def _room(self, hotel, number):
        category = (
            self.category
            if hotel.pk == self.hotel.pk
            else self.outside_category
        )
        return self.Room.objects.create(
            hotel=hotel,
            room_number=number,
            category=category,
        )

    def test_actual_room_nights_are_deduplicated_and_hotel_scoped(self):
        from billing.models import Charge, Folio, Reservation
        from inventory.models import Department
        from core.services.room_occupancy import build_room_occupancy_analytics
        from datetime import timedelta
        from django.utils import timezone

        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        room = self._room(self.hotel, "101")
        outside_room = self._room(self.outside_hotel, "201")
        reservation = Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            room=room,
            check_in_date=yesterday,
            check_out_date=today,
            status="CHECKED_OUT",
        )
        folio = Folio.objects.create(
            folio_type="ROOM",
            hotel=self.hotel,
            room=room,
            guest=self.guest,
            reservation=reservation,
            check_in_at=timezone.now() - timedelta(days=1),
            check_out_at=timezone.now(),
            is_closed=True,
        )
        department = Department.objects.create(
            hotel=self.hotel,
            code="FD-OCC",
            name="Occupancy Front Desk",
            department_type="FRONTDESK",
        )
        for reference in ("ROOM-ORIGINAL", "ROOM-CHANGE"):
            Charge.objects.create(
                folio=folio,
                description="Room night",
                department=department,
                amount="100.00",
                reference=reference,
                room_night_date=yesterday,
                room_rate="100.00",
            )

        outside_guest = __import__("billing.models", fromlist=["Guest"]).Guest.objects.create(
            hotel=self.outside_hotel,
            first_name="Outside",
            last_name="Guest",
        )
        outside_reservation = Reservation.objects.create(
            hotel=self.outside_hotel,
            guest=outside_guest,
            room_category=self.outside_category,
            room=outside_room,
            check_in_date=yesterday,
            check_out_date=today,
            status="CHECKED_OUT",
        )
        outside_folio = Folio.objects.create(
            folio_type="ROOM",
            hotel=self.outside_hotel,
            room=outside_room,
            guest=outside_guest,
            reservation=outside_reservation,
            check_in_at=timezone.now() - timedelta(days=1),
            check_out_at=timezone.now(),
            is_closed=True,
        )
        outside_department = Department.objects.create(
            hotel=self.outside_hotel,
            code="FD-OCC",
            name="Outside Front Desk",
            department_type="FRONTDESK",
        )
        Charge.objects.create(
            folio=outside_folio,
            description="Room night",
            department=outside_department,
            amount="100.00",
            room_night_date=yesterday,
            room_rate="100.00",
        )

        result = build_room_occupancy_analytics(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=yesterday,
            date_to=today,
            today=today,
        )

        self.assertEqual(result["actual_room_nights"], 1)
        self.assertEqual(result["forecast_room_nights"], 0)
        self.assertEqual(result["gross_room_inventory"], 1)
        self.assertEqual(result["actual_occupancy_percent"], 100.0)
        self.assertEqual(len(result["daily_rows"]), 2)

    def test_forecast_counts_room_allocations_once_and_uses_gross_inventory(self):
        from billing.models import GroupReservation, GroupReservationRoom, Reservation, ReservationRoomAllocation
        from core.services.room_occupancy import build_room_occupancy_analytics
        from datetime import timedelta
        from django.utils import timezone

        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        self._room(self.hotel, "101")
        self._room(self.hotel, "102")
        legacy = Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=tomorrow,
            check_out_date=tomorrow + timedelta(days=2),
            status="RESERVED",
        )
        parent = Reservation.objects.create(
            hotel=self.hotel,
            guest=self.guest,
            room_category=self.category,
            check_in_date=tomorrow,
            check_out_date=tomorrow + timedelta(days=2),
            status="RESERVED",
        )
        ReservationRoomAllocation.objects.create(
            reservation=parent,
            allocation_number=1,
            guest=self.guest,
            check_in_date=tomorrow,
            check_out_date=tomorrow + timedelta(days=2),
            status="RESERVED",
        )
        company = __import__("billing.models", fromlist=["Company"]).Company.objects.create(
            hotel=self.hotel,
            name="Occupancy Analytics Company",
        )
        group = GroupReservation.objects.create(
            hotel=self.hotel,
            company=company,
            reference="GRP-OCC-1",
            check_in_date=tomorrow,
            check_out_date=tomorrow + timedelta(days=2),
            status="CONFIRMED",
        )
        GroupReservationRoom.objects.create(
            group_reservation=group,
            guest=self.guest,
            room_category=self.category,
            check_in_date=tomorrow,
            check_out_date=tomorrow + timedelta(days=2),
            status="RESERVED",
        )

        result = build_room_occupancy_analytics(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=today,
            date_to=tomorrow + timedelta(days=1),
            today=today,
        )

        self.assertEqual(result["gross_room_inventory"], 2)
        self.assertEqual(result["forecast_room_nights"], 6)
        forecast_days = [
            row for row in result["daily_rows"]
            if row["basis"] == "FORECAST"
        ]
        self.assertEqual([row["occupied_room_count"] for row in forecast_days], [0, 3, 3])
        self.assertEqual([row["occupancy_percent"] for row in forecast_days], [0.0, 150.0, 150.0])


class ManagementReservationsOccupancyViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Reservations Occupancy View Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Reservations Occupancy Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Reservations Occupancy Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Reservations Occupancy Hotel"
        )

    def test_director_can_view_organization_reservations_occupancy(self):
        director = User.objects.create_user(
            username="reservations-occupancy-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get("/management/reservations-occupancy/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
        self.assertTemplateUsed(response, "management/reservations_occupancy.html")

    def test_general_manager_is_limited_to_assigned_hotels(self):
        general_manager = User.objects.create_user(
            username="reservations-occupancy-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(general_manager)
        response = self.client.get("/management/reservations-occupancy/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [hotel.pk for hotel in response.context["hotels"]],
            [self.hotel_two.pk],
        )
        self.assertEqual(response.context["gross_room_inventory"], 0)

    def test_operational_frontdesk_user_cannot_open_management_report(self):
        frontdesk_user = User.objects.create_user(
            username="reservations-occupancy-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(frontdesk_user)
        response = self.client.get("/management/reservations-occupancy/")
        self.assertEqual(response.status_code, 403)

    def test_inaccessible_hotel_filter_does_not_expand_scope(self):
        director = User.objects.create_user(
            username="reservations-occupancy-isolation",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            "/management/reservations-occupancy/",
            {"hotel": str(self.outside_hotel.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )


class ManagementGuest360AccessTests(TestCase):
    def setUp(self):
        from billing.models import Guest

        self.organization = Organization.objects.create(name="Guest 360 Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization, name="Guest 360 Hotel One"
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization, name="Guest 360 Hotel Two"
        )
        self.outside_hotel = Hotel.objects.create(name="Guest 360 Outside Hotel")
        self.guest_one = Guest.objects.create(
            hotel=self.hotel_one,
            first_name="Ada",
            last_name="Lovelace",
            phone="08000000001",
            email="ada@example.test",
        )
        self.guest_two = Guest.objects.create(
            hotel=self.hotel_two,
            first_name="Grace",
            last_name="Hopper",
            phone="08000000002",
        )
        self.outside_guest = Guest.objects.create(
            hotel=self.outside_hotel,
            first_name="Outside",
            last_name="Guest",
            phone="08000000003",
        )

    def test_director_guest_search_is_organization_scoped(self):
        director = User.objects.create_user(
            username="guest360-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get("/management/guests/", {"q": "0800000000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {guest.pk for guest in response.context["guest_results"]},
            {self.guest_one.pk, self.guest_two.pk},
        )
        self.assertNotContains(response, "Outside Guest")

    def test_guest_directory_shows_recent_guests_without_search_term(self):
        director = User.objects.create_user(
            username="guest360-directory-default",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get("/management/guests/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {guest.pk for guest in response.context["guest_results"]},
            {self.guest_one.pk, self.guest_two.pk},
        )

    def test_general_manager_guest_search_is_assigned_hotel_scoped(self):
        general_manager = User.objects.create_user(
            username="guest360-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel_two)
        self.client.force_login(general_manager)
        response = self.client.get("/management/guests/", {"q": "Grace"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [guest.pk for guest in response.context["guest_results"]],
            [self.guest_two.pk],
        )

    def test_director_can_open_guest_profile_within_scope(self):
        director = User.objects.create_user(
            username="guest360-profile-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            f"/management/guests/{self.guest_one.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["guest"], self.guest_one)

    def test_guest_profile_cannot_be_opened_outside_management_scope(self):
        director = User.objects.create_user(
            username="guest360-profile-isolation",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            f"/management/guests/{self.outside_guest.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_frontdesk_role_cannot_use_management_guest_search(self):
        frontdesk_user = User.objects.create_user(
            username="guest360-frontdesk-denied",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(frontdesk_user)
        response = self.client.get("/management/guests/", {"q": "Ada"})
        self.assertEqual(response.status_code, 403)

    def test_director_deposit_receipt_uses_existing_deposit_service(self):
        from unittest.mock import patch

        director = User.objects.create_user(
            username="guest360-deposit-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        with patch("billing.services.guest_deposit.receive_guest_deposit") as receive:
            response = self.client.post(
                f"/management/guests/{self.guest_one.pk}/",
                {
                    "amount": "25000.00",
                    "method": "CASH",
                    "reference": "",
                    "note": "Management guest deposit",
                },
            )
        self.assertRedirects(
            response,
            f"/management/guests/{self.guest_one.pk}/",
            fetch_redirect_response=False,
        )
        receive.assert_called_once()
        self.assertEqual(receive.call_args.kwargs["guest"], self.guest_one)
        self.assertEqual(receive.call_args.kwargs["method"], "CASH")
        self.assertEqual(receive.call_args.kwargs["user"], director)



class ManagementCorporateCompanyDetailTests(TestCase):
    def setUp(self):
        from billing.models import Company

        self.organization = Organization.objects.create(name="Corporate Detail Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization, name="Corporate Detail Hotel"
        )
        self.other_hotel = Hotel.objects.create(
            name="Corporate Detail Outside Hotel"
        )
        self.company = Company.objects.create(
            hotel=self.hotel, name="Example Corporate Client",
            contact_person="Ada Example", phone="08000000000",
        )
        self.outside_company = Company.objects.create(
            hotel=self.other_hotel, name="Outside Corporate Client"
        )

    def test_director_can_open_company_detail_within_organization(self):
        director = User.objects.create_user(
            username="corporate-detail-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            f"/management/companies/{self.company.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["company"], self.company)
        self.assertTemplateUsed(response, "management/company_detail.html")

    def test_director_cannot_open_company_outside_organization(self):
        director = User.objects.create_user(
            username="corporate-detail-isolation",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(director)
        response = self.client.get(
            f"/management/companies/{self.outside_company.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_general_manager_cannot_open_company_outside_assigned_hotels(self):
        general_manager = User.objects.create_user(
            username="corporate-detail-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        general_manager.assigned_hotels.add(self.hotel)
        self.client.force_login(general_manager)
        response = self.client.get(
            f"/management/companies/{self.outside_company.pk}/"
        )
        self.assertEqual(response.status_code, 404)

    def test_operational_user_cannot_open_company_detail(self):
        operational_user = User.objects.create_user(
            username="corporate-detail-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel,
        )
        self.client.force_login(operational_user)
        response = self.client.get(
            f"/management/companies/{self.company.pk}/"
        )
        self.assertEqual(response.status_code, 403)
