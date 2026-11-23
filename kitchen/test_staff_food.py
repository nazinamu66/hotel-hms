from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory.models import (
    Department, Hotel, Organization, Product, Stock, StockCostLayer, StockMovement,
)
from kitchen.models import (
    StaffFoodProduction, StaffFoodIngredientUsage,
    Recipe, RecipeVersion, RecipeVersionItem,
)
from kitchen.services.staff_food import execute_staff_food_production


User = get_user_model()


class StaffFoodProductionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Staff Food Org")
        cls.hotel = Hotel.objects.create(organization=cls.organization, name="Staff Food Hotel")
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN",
            code="SFK", name="Staff Food Kitchen",
        )
        cls.cook = User.objects.create_user(
            username="staff_food_cook", password="test-password",
            role="KITCHEN", hotel=cls.hotel, department=cls.kitchen,
        )
        cls.manager = User.objects.create_user(
            username="staff_food_manager", password="test-password",
            role="MANAGER", hotel=cls.hotel,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel, name="Staff Food Rice", sku="SF-RICE",
            product_type="RAW", base_unit="kg", cost_price=Decimal("100.00"),
        )
        cls.ingredient.departments.add(cls.kitchen)

    def setUp(self):
        self.stock = Stock.objects.create(
            product=self.ingredient, department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        self.layer = StockCostLayer.objects.create(
            product=self.ingredient, department=self.kitchen,
            purchase_item=None, source_reference="SF-OPEN",
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("50.0000"),
        )

    def make_production(self, **overrides):
        values = {
            "department": self.kitchen,
            "production_date": "2026-11-23",
            "meal_name": "Rice and stew",
            "status": "PREPARED",
            "portions_prepared": 8,
            "portions_served": 7,
            "recorded_by": self.cook,
        }
        values.update(overrides)
        return StaffFoodProduction.objects.create(**values)

    @patch("kitchen.services.staff_food.post_staff_food_consumption")
    def test_manual_staff_food_consumes_fifo_and_posts_staff_meal_expense(self, post_entry):
        production = self.make_production()
        execute_staff_food_production(
            production=production,
            ingredient_entries=[{
                "ingredient_id": self.ingredient.pk,
                "actual_quantity": "2.000",
                "unit": "kg",
            }],
            user=self.cook,
        )

        self.stock.refresh_from_db()
        self.layer.refresh_from_db()
        production.refresh_from_db()
        usage = StaffFoodIngredientUsage.objects.get(production=production)

        self.assertEqual(self.stock.quantity, Decimal("8.000"))
        self.assertEqual(self.layer.quantity_remaining, Decimal("8.000"))
        self.assertEqual(usage.actual_quantity, Decimal("2.000"))
        self.assertEqual(usage.total_cost, Decimal("100.00"))
        self.assertEqual(production.total_cost, Decimal("100.00"))
        self.assertEqual(
            StockMovement.objects.filter(reference=f"STAFF-FOOD-{production.pk}-{self.ingredient.pk}").count(),
            1,
        )
        post_entry.assert_called_once()
        self.assertEqual(post_entry.call_args.kwargs["production"].pk, production.pk)
        self.assertEqual(post_entry.call_args.kwargs["amount"], Decimal("100.00"))
        self.assertEqual(post_entry.call_args.kwargs["user"], self.cook)
        self.assertFalse(
            Stock.objects.filter(
                product__product_type="FOOD", department=self.kitchen
            ).exists()
        )

    @patch("kitchen.services.staff_food.post_staff_food_consumption")
    def test_insufficient_stock_rolls_back_all_staff_food_consumption(self, post_entry):
        production = self.make_production()
        with self.assertRaises(ValidationError):
            execute_staff_food_production(
                production=production,
                ingredient_entries=[{
                    "ingredient_id": self.ingredient.pk,
                    "actual_quantity": "12.000",
                    "unit": "kg",
                }],
                user=self.cook,
            )
        self.stock.refresh_from_db()
        self.layer.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.000"))
        self.assertEqual(self.layer.quantity_remaining, Decimal("10.000"))
        self.assertFalse(StaffFoodIngredientUsage.objects.filter(production=production).exists())
        post_entry.assert_not_called()

    @patch("kitchen.services.staff_food.post_staff_food_consumption")
    def test_no_meal_day_is_recorded_without_stock_or_accounting(self, post_entry):
        production = self.make_production(
            status="NOT_PREPARED", meal_name="", portions_prepared=0, portions_served=0,
        )
        execute_staff_food_production(
            production=production, ingredient_entries=[], user=self.cook,
        )
        self.stock.refresh_from_db()
        self.layer.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.000"))
        self.assertEqual(self.layer.quantity_remaining, Decimal("10.000"))
        self.assertEqual(production.total_cost, Decimal("0.00"))
        post_entry.assert_not_called()

    def test_staff_food_page_is_kitchen_only_and_link_is_available(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("kitchen_staff_food"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff Food Register")
        dashboard = self.client.get(reverse("kitchen_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Record daily staff meals")

        self.client.force_login(self.manager)
        denied = self.client.get(reverse("kitchen_staff_food"))
        self.assertEqual(denied.status_code, 403)
        management_dashboard = self.client.get(reverse("kitchen_dashboard"))
        self.assertEqual(management_dashboard.status_code, 200)
        self.assertNotContains(management_dashboard, "Record daily staff meals")

    @patch("kitchen.services.staff_food.post_staff_food_consumption")
    def test_manual_staff_food_can_be_recorded_from_kitchen_page(self, post_entry):
        self.client.force_login(self.cook)
        response = self.client.post(reverse("kitchen_staff_food"), {
            "production_date": "2026-11-23",
            "meal_name": "Rice and stew",
            "mode": "MANUAL",
            "portions_prepared": "8",
            "portions_served": "7",
            "notes": "",
            "ingredients-TOTAL_FORMS": "1",
            "ingredients-INITIAL_FORMS": "0",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-ingredient": str(self.ingredient.pk),
            "ingredients-0-actual_quantity": "2.000",
            "ingredients-0-unit": "",
            "ingredients-0-variance_reason": "",
            "ingredients-0-DELETE": "",
        })
        self.assertEqual(response.status_code, 302)
        production = StaffFoodProduction.objects.get(
            department=self.kitchen, meal_name="Rice and stew"
        )
        self.assertEqual(production.total_cost, Decimal("100.00"))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8.000"))
        post_entry.assert_called_once()

    @patch("kitchen.services.staff_food.post_staff_food_consumption")
    def test_staff_food_can_use_an_approved_recipe_snapshot(self, post_entry):
        finished = Product.objects.create(
            hotel=self.hotel, name="Staff Food Rice Plate", sku="SF-PLATE",
            product_type="FOOD", base_unit="portion",
        )
        recipe = Recipe.objects.create(name="Staff Rice Recipe", product=finished)
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status="APPROVED",
            created_by=self.cook, approved_by=self.cook,
        )
        RecipeVersionItem.objects.create(
            recipe_version=version, ingredient=self.ingredient,
            quantity=Decimal("0.250"), unit="kg",
            control_mode="STRICT", tolerance_percent=Decimal("0.00"),
        )
        recipe.active_version = version
        recipe.save(update_fields=["active_version"])
        production = self.make_production(recipe=recipe, recipe_version=version)

        execute_staff_food_production(
            production=production,
            ingredient_entries=[{
                "ingredient_id": self.ingredient.pk,
                "actual_quantity": "2.000",
                "unit": "kg",
            }],
            user=self.cook,
        )

        usage = StaffFoodIngredientUsage.objects.get(production=production)
        self.assertEqual(usage.expected_quantity, Decimal("2.000"))
        self.assertEqual(usage.actual_quantity, Decimal("2.000"))
        self.assertEqual(usage.total_cost, Decimal("100.00"))
        post_entry.assert_called_once()
