from datetime import date
from decimal import Decimal

from django.test import TestCase

from billing.models import (
    Charge,
    Company,
    CompanyDeposit,
    Folio,
    GroupReservation,
    GroupReservationRoom,
    Guest,
)
from billing.services.company_invoices import create_company_invoice, issue_company_invoice
from core.services.corporate_performance import build_corporate_performance
from inventory.models import Department, Hotel, Organization
from rooms.models import RoomCategory


class CorporatePerformanceServiceTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Corporate Reporting Org")
        self.hotel = Hotel.objects.create(
            organization=organization,
            name="Corporate Reporting Hotel",
        )
        self.outside_hotel = Hotel.objects.create(
            name="Outside Corporate Reporting Hotel",
        )
        self.company = Company.objects.create(
            hotel=self.hotel,
            name="Alpha Limited",
        )
        self.other_company = Company.objects.create(
            hotel=self.outside_hotel,
            name="Outside Limited",
        )
        self.group = GroupReservation.objects.create(
            hotel=self.hotel,
            company=self.company,
            reference="CORP-REPORT-001",
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 3),
            status="COMPLETED",
        )
        self.guest = Guest.objects.create(
            hotel=self.hotel,
            first_name="Corporate",
            last_name="Guest",
        )
        self.folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="WALKIN",
            guest=self.guest,
        )
        self.category = RoomCategory.objects.create(
            hotel=self.hotel,
            name="Corporate Reporting Standard",
        )
        self.outside_category = RoomCategory.objects.create(
            hotel=self.outside_hotel,
            name="Outside Corporate Reporting Standard",
        )
        self.allocation = GroupReservationRoom.objects.create(
            group_reservation=self.group,
            guest=self.guest,
            room_category=self.category,
            folio=self.folio,
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 3),
        )
        self.department = Department.objects.create(
            hotel=self.hotel,
            code="CORP-RPT",
            name="Corporate Reporting Frontdesk",
            department_type="FRONTDESK",
        )

    def _charge(self, *, amount, billing_party="COMPANY", night_date=None, rate_source=""):
        return Charge.objects.create(
            folio=self.folio,
            department=self.department,
            description="Corporate reporting test charge",
            amount=Decimal(amount),
            billing_party=billing_party,
            group_room_allocation=self.allocation,
            room_night_date=night_date,
            room_rate=Decimal(amount) if night_date else None,
            room_rate_source=rate_source,
        )

    def test_company_revenue_comes_from_company_charges_not_invoice_or_payments(self):
        self._charge(
            amount="70000.00",
            night_date=date(2026, 11, 1),
            rate_source="GROUP",
        )
        self._charge(amount="5000.00", billing_party="INDIVIDUAL")

        invoice = create_company_invoice(group=self.group, user=None)
        issue_company_invoice(invoice=invoice, user=None)

        CompanyDeposit.objects.create(
            hotel=self.hotel,
            company=self.company,
            balance=Decimal("20000.00"),
        )

        report = build_corporate_performance(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["corporate_revenue"], Decimal("70000.00"))
        self.assertEqual(report["corporate_room_nights"], 1)
        self.assertEqual(report["average_negotiated_rate"], Decimal("70000.00"))
        self.assertEqual(report["invoice_outstanding"], Decimal("70000.00"))
        self.assertEqual(report["deposits_held"], Decimal("20000.00"))
        self.assertEqual(report["unbilled_company_charges"], Decimal("0.00"))
        self.assertEqual(report["active_company_count"], 1)
        self.assertEqual(report["companies_with_business"], 1)

    def test_unbilled_company_charges_are_reported_separately(self):
        self._charge(
            amount="12000.00",
            night_date=date(2026, 11, 2),
            rate_source="OVERRIDE",
        )

        report = build_corporate_performance(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["corporate_revenue"], Decimal("12000.00"))
        self.assertEqual(report["unbilled_company_charges"], Decimal("12000.00"))
        self.assertEqual(report["invoice_outstanding"], Decimal("0.00"))

    def test_company_and_hotel_scope_excludes_outside_hotel(self):
        outside_group = GroupReservation.objects.create(
            hotel=self.outside_hotel,
            company=self.other_company,
            reference="CORP-OUTSIDE-001",
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 2),
        )
        outside_guest = Guest.objects.create(
            hotel=self.outside_hotel,
            first_name="Outside",
            last_name="Guest",
        )
        outside_folio = Folio.objects.create(
            hotel=self.outside_hotel,
            folio_type="WALKIN",
            guest=outside_guest,
        )
        outside_allocation = GroupReservationRoom.objects.create(
            group_reservation=outside_group,
            guest=outside_guest,
            room_category=self.outside_category,
            folio=outside_folio,
            check_in_date=date(2026, 11, 1),
            check_out_date=date(2026, 11, 2),
        )
        Charge.objects.create(
            folio=outside_folio,
            department=Department.objects.create(
                hotel=self.outside_hotel,
                code="CORP-OUT",
                name="Outside Frontdesk",
                department_type="FRONTDESK",
            ),
            description="Outside company charge",
            amount=Decimal("90000.00"),
            billing_party="COMPANY",
            group_room_allocation=outside_allocation,
            room_night_date=date(2026, 11, 1),
            room_rate=Decimal("90000.00"),
            room_rate_source="GROUP",
        )

        report = build_corporate_performance(
            hotels=Hotel.objects.filter(pk=self.hotel.pk),
            date_from=date(2026, 11, 1),
            date_to=date(2026, 11, 30),
            today=date(2026, 11, 19),
        )

        self.assertEqual(report["corporate_revenue"], Decimal("0.00"))
        self.assertEqual(report["active_company_count"], 1)
        self.assertEqual(report["company_ranking_rows"], [])


class CorporatePerformanceViewTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Corporate View Org")
        self.hotel_one = Hotel.objects.create(
            organization=self.organization,
            name="Corporate View Hotel One",
        )
        self.hotel_two = Hotel.objects.create(
            organization=self.organization,
            name="Corporate View Hotel Two",
        )
        self.outside_hotel = Hotel.objects.create(name="Outside Corporate View Hotel")

    def test_director_can_view_organization_corporate_performance(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="corporate-view-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(user)

        response = self.client.get("/management/corporate/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
        self.assertContains(response, "Corporate Performance")

    def test_general_manager_sees_only_assigned_hotels(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="corporate-view-gm",
            password="test-password",
            role="GENERAL_MANAGER",
            organization=self.organization,
        )
        user.assigned_hotels.add(self.hotel_two)
        self.client.force_login(user)

        response = self.client.get("/management/corporate/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [hotel.pk for hotel in response.context["hotels"]],
            [self.hotel_two.pk],
        )

    def test_operational_user_cannot_open_corporate_management_report(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="corporate-view-frontdesk",
            password="test-password",
            role="FRONTDESK",
            hotel=self.hotel_one,
        )
        self.client.force_login(user)

        response = self.client.get("/management/corporate/")

        self.assertEqual(response.status_code, 403)

    def test_inaccessible_hotel_filter_does_not_expand_reporting_scope(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="corporate-view-isolation",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.client.force_login(user)

        response = self.client.get(
            "/management/corporate/",
            {"hotel": str(self.outside_hotel.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {hotel.pk for hotel in response.context["hotels"]},
            {self.hotel_one.pk, self.hotel_two.pk},
        )
