from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.models import (
    Charge,
    Company,
    CompanyServicePolicy,
    GroupReservation,
    Folio,
)
from inventory.models import Department, Hotel, Organization


class CompanyGroupFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Group Booking Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Group Booking Test Hotel",
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="GBT",
            name="Group Booking Test Frontdesk",
            department_type="FRONTDESK",
        )
        cls.company = Company.objects.create(
            hotel=cls.hotel,
            name="Example Corporate Ltd",
            contact_person="Jane Doe",
            email="travel@example.test",
        )
        cls.group = GroupReservation.objects.create(
            hotel=cls.hotel,
            company=cls.company,
            reference="CORP-2026-001",
            check_in_date=date(2026, 12, 1),
            check_out_date=date(2026, 12, 8),
            status="CONFIRMED",
        )

    def test_company_is_scoped_to_hotel(self):
        self.assertEqual(self.company.hotel, self.hotel)
        self.assertEqual(str(self.company), "Example Corporate Ltd")

    def test_group_reservation_rejects_company_from_another_hotel(self):
        other_org = Organization.objects.create(name="Other Group Org")
        other_hotel = Hotel.objects.create(
            organization=other_org,
            name="Other Group Hotel",
        )
        other_company = Company.objects.create(
            hotel=other_hotel,
            name="Other Corporate Ltd",
        )
        group = GroupReservation(
            hotel=self.hotel,
            company=other_company,
            reference="INVALID-CROSS-HOTEL",
            check_in_date=date(2026, 12, 1),
            check_out_date=date(2026, 12, 8),
        )
        with self.assertRaises(ValidationError):
            group.full_clean()

    def test_group_reservation_rejects_invalid_date_range(self):
        group = GroupReservation(
            hotel=self.hotel,
            company=self.company,
            reference="INVALID-DATES",
            check_in_date=date(2026, 12, 8),
            check_out_date=date(2026, 12, 1),
        )
        with self.assertRaises(ValidationError):
            group.full_clean()

    def test_service_policy_preserves_company_coverage_and_allowance(self):
        policy = CompanyServicePolicy.objects.create(
            group_reservation=self.group,
            service_code="LAUNDRY",
            service_name="Laundry allowance",
            coverage="COVERED",
            amount_limit_per_occupant=Decimal("15000.00"),
            quantity_limit_per_occupant=Decimal("10.00"),
        )
        self.assertEqual(policy.coverage, "COVERED")
        self.assertEqual(
            policy.amount_limit_per_occupant,
            Decimal("15000.00"),
        )

    def test_new_charges_default_to_individual_responsibility(self):
        folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="WALKIN",
        )
        charge = Charge.objects.create(
            folio=folio,
            department=self.department,
            description="Personal laundry",
            amount=Decimal("2500.00"),
        )
        self.assertEqual(charge.billing_party, "INDIVIDUAL")
        self.assertIsNone(charge.group_room_allocation)
