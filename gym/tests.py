from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from django.core.exceptions import ValidationError

from .models import GymMembership, GymMembershipPlan


class GymMembershipDateTests(SimpleTestCase):
    def test_daily_membership_ends_on_start_date(self):
        self.assertEqual(
            GymMembership.calculate_end_date(date(2026, 11, 26), GymMembershipPlan.DAILY),
            date(2026, 11, 26),
        )

    def test_weekly_membership_covers_seven_calendar_days(self):
        self.assertEqual(
            GymMembership.calculate_end_date(date(2026, 11, 26), GymMembershipPlan.WEEKLY),
            date(2026, 12, 2),
        )

    def test_monthly_membership_ends_day_before_next_month(self):
        self.assertEqual(
            GymMembership.calculate_end_date(date(2026, 1, 31), GymMembershipPlan.MONTHLY),
            date(2026, 2, 27),
        )

    def test_quarterly_membership_ends_day_before_three_month_anniversary(self):
        self.assertEqual(
            GymMembership.calculate_end_date(date(2026, 11, 26), GymMembershipPlan.QUARTERLY),
            date(2027, 2, 25),
        )

    def test_annual_membership_ends_day_before_anniversary(self):
        self.assertEqual(
            GymMembership.calculate_end_date(date(2026, 11, 26), GymMembershipPlan.ANNUAL),
            date(2027, 11, 25),
        )

    def test_unknown_package_is_rejected(self):
        with self.assertRaises(ValidationError):
            GymMembership.calculate_end_date(date(2026, 11, 26), "UNKNOWN")


from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import Account
from billing.models import Charge, ChargePaymentAllocation, Payment
from inventory.models import Department, Hotel, HotelFeature
from gym.models import GymMember, GymMembershipPlan
from gym.services.memberships import create_paid_membership


class GymMembershipPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hotel = Hotel.objects.create(name="Gym Payment Test Hotel")
        HotelFeature.objects.get_or_create(
            hotel=cls.hotel, feature="GYM", defaults={"is_active": True}
        )
        cls.department = Department.objects.get(
            hotel=cls.hotel, department_type="GYM"
        )
        cls.user = get_user_model().objects.create_user(
            username="gym-frontdesk-test",
            password="test-password",
            role="FRONTDESK",
            department=cls.department,
        )
        cls.member = GymMember.objects.create(
            hotel=cls.hotel,
            first_name="Test",
            last_name="Member",
            phone="08000000000",
            created_by=cls.user,
        )
        cls.plan = GymMembershipPlan.objects.create(
            hotel=cls.hotel,
            package=GymMembershipPlan.MONTHLY,
            price="15000.00",
        )

    def test_cash_checkout_creates_paid_membership_and_balanced_billing_records(self):
        membership = create_paid_membership(
            member_id=self.member.pk,
            plan_id=self.plan.pk,
            hotel=self.hotel,
            user=self.user,
            payment_method="CASH",
            start_date=date(2026, 11, 27),
        )

        self.assertEqual(membership.status, "ACTIVE")
        self.assertEqual(membership.amount_snapshot, Decimal("15000.00"))
        self.assertEqual(membership.end_date, date(2026, 12, 26))
        self.assertIsNotNone(membership.billing_payment_id)

        payment = membership.billing_payment
        self.assertEqual(payment.method, "CASH")
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertEqual(
            ChargePaymentAllocation.objects.filter(payment=payment).count(), 1
        )
        self.assertEqual(
            Charge.objects.filter(reference=f"GYM-MEM-{membership.pk}").count(), 1
        )
        journal = membership.hotel.journal_entries.get(
            reference=f"GYM-MEM-{membership.pk}", entry_type="SALE"
        )
        self.assertEqual(journal.lines.count(), 2)
        self.assertTrue(
            Account.objects.filter(
                hotel=self.hotel, system_key="gym_revenue", is_system=True
            ).exists()
        )

    def test_front_desk_can_open_checkout_and_complete_cash_membership(self):
        self.client.force_login(self.user)

        checkout_url = f"/gym/members/{self.member.pk}/membership/new/"
        response = self.client.get(checkout_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New membership")

        response = self.client.post(checkout_url, {
            "plan": self.plan.pk,
            "start_date": "2026-11-27",
            "payment_method": "CASH",
            "bank_account": "",
            "payment_reference": "",
        })
        self.assertEqual(response.status_code, 302)
        membership = GymMembership.objects.get(member=self.member)
        self.assertEqual(response.url, f"/gym/memberships/{membership.pk}/receipt/")

        receipt = self.client.get(response.url)
        self.assertEqual(receipt.status_code, 200)
        self.assertContains(receipt, "Gym Membership Payment Receipt")
        self.assertContains(receipt, "15,000.00")

        detail = self.client.get(f"/gym/members/{self.member.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Membership history")
        self.assertContains(detail, "View receipt")

    def test_invalid_payment_method_does_not_create_membership(self):
        with self.assertRaises(ValidationError):
            create_paid_membership(
                member_id=self.member.pk,
                plan_id=self.plan.pk,
                hotel=self.hotel,
                user=self.user,
                payment_method="CHEQUE",
            )
        self.assertEqual(self.member.memberships.count(), 0)

    def test_zero_price_package_cannot_be_activated(self):
        self.plan.price = "0.00"
        self.plan.save(update_fields=["price"])
        with self.assertRaises(ValidationError):
            create_paid_membership(
                member_id=self.member.pk,
                plan_id=self.plan.pk,
                hotel=self.hotel,
                user=self.user,
                payment_method="CASH",
            )
        self.assertEqual(self.member.memberships.count(), 0)


class GymGateVerificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hotel = Hotel.objects.create(name="Gym Gate Test Hotel")
        HotelFeature.objects.get_or_create(
            hotel=cls.hotel, feature="GYM", defaults={"is_active": True}
        )
        cls.department = Department.objects.get(
            hotel=cls.hotel, department_type="GYM"
        )
        User = get_user_model()
        cls.security = User.objects.create_user(
            username="gym-gate-security", password="test-password",
            role="SECURITY", department=cls.department,
        )
        cls.frontdesk = User.objects.create_user(
            username="gym-gate-frontdesk", password="test-password",
            role="FRONTDESK", department=cls.department,
        )
        cls.member = GymMember.objects.create(
            hotel=cls.hotel, first_name="Gate", last_name="Member",
            phone="08011111111", created_by=cls.frontdesk,
        )
        cls.plan = GymMembershipPlan.objects.create(
            hotel=cls.hotel, package=GymMembershipPlan.MONTHLY, price="12000.00",
        )

    def create_membership(self, start_date=None):
        return create_paid_membership(
            member_id=self.member.pk, plan_id=self.plan.pk,
            hotel=self.hotel, user=self.frontdesk, payment_method="CASH",
            start_date=start_date or date(2026, 11, 27),
        )

    def test_security_can_check_member_in_and_out(self):
        from gym.models import GymVisit
        from gym.services.gate import check_in_member, check_out_member

        membership = self.create_membership()
        visit = check_in_member(
            membership_id=membership.pk, hotel=self.hotel, user=self.security
        )
        self.assertIsNone(visit.exited_at)
        self.assertEqual(visit.checked_in_by, self.security)

        closed = check_out_member(
            visit_id=visit.pk, hotel=self.hotel, user=self.security
        )
        self.assertIsNotNone(closed.exited_at)
        self.assertEqual(closed.checked_out_by, self.security)
        self.assertEqual(GymVisit.objects.filter(membership=membership).count(), 1)

    def test_duplicate_open_visit_is_rejected(self):
        from django.core.exceptions import ValidationError
        from gym.services.gate import check_in_member

        membership = self.create_membership()
        check_in_member(membership_id=membership.pk, hotel=self.hotel, user=self.security)
        with self.assertRaises(ValidationError):
            check_in_member(membership_id=membership.pk, hotel=self.hotel, user=self.security)

    def test_expired_membership_cannot_check_in(self):
        from django.core.exceptions import ValidationError
        from gym.services.gate import check_in_member

        membership = self.create_membership(start_date=date(2026, 10, 1))
        with self.assertRaises(ValidationError):
            check_in_member(membership_id=membership.pk, hotel=self.hotel, user=self.security)

    def test_gate_lookup_and_entry_are_available_to_security(self):
        membership = self.create_membership()
        self.client.force_login(self.security)
        response = self.client.post("/gym/gate/", {
            "action": "lookup", "code": membership.member.member_number,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, membership.member.full_name)
        self.assertContains(response, "Record entry")

        response = self.client.post("/gym/gate/", {
            "action": "check_in", "membership_id": membership.pk,
        })
        self.assertEqual(response.status_code, 302)
        visit = membership.visits.get(exited_at__isnull=True)
        self.assertEqual(visit.checked_in_by, self.security)

        response = self.client.post("/gym/gate/", {
            "action": "check_out", "visit_id": visit.pk,
        })
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertIsNotNone(visit.exited_at)
        self.assertEqual(visit.checked_out_by, self.security)

    def test_membership_from_another_hotel_cannot_be_checked_in(self):
        from django.core.exceptions import ValidationError
        from gym.services.gate import check_in_member

        membership = self.create_membership()
        other_hotel = Hotel.objects.create(name="Different Gym Hotel")
        with self.assertRaises(ValidationError):
            check_in_member(
                membership_id=membership.pk, hotel=other_hotel, user=self.security
            )
