from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from accounting.services.postings.staff_food import post_staff_food_consumption


class StaffFoodAccountingPostingTests(SimpleTestCase):
    @patch("accounting.services.postings.staff_food.record_transaction")
    def test_posts_fifo_cost_to_staff_meals_expense_and_inventory_asset(self, record):
        hotel = object()
        user = object()
        production = SimpleNamespace(
            pk=42,
            department=SimpleNamespace(hotel=hotel),
            meal_name="Rice and stew",
            production_date="2026-11-23",
        )

        post_staff_food_consumption(
            production=production,
            amount=Decimal("123.456"),
            user=user,
        )

        record.assert_called_once_with(
            debit_system_key="staff_meals_expense",
            credit_system_key="inventory_asset",
            amount=Decimal("123.46"),
            description="Staff food - Rice and stew (2026-11-23)",
            hotel=hotel,
            created_by=user,
            entry_type="NORMAL",
            reference="STAFF-FOOD-42",
        )

    @patch("accounting.services.postings.staff_food.record_transaction")
    def test_rejects_zero_cost_without_creating_journal_entry(self, record):
        production = SimpleNamespace(
            pk=43,
            department=SimpleNamespace(hotel=object()),
            meal_name="Beans",
            production_date="2026-11-23",
        )
        with self.assertRaises(ValidationError):
            post_staff_food_consumption(
                production=production,
                amount=Decimal("0.00"),
                user=None,
            )
        record.assert_not_called()
