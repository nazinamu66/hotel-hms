from datetime import date

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
