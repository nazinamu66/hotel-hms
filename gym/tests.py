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
