from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .unit_conversion import convert_quantity


class RecipeUnitConversionTests(SimpleTestCase):
    def test_grams_convert_to_kilograms(self):
        self.assertEqual(convert_quantity("250", "g", "kg"), Decimal("0.250"))

    def test_kilograms_convert_to_grams(self):
        self.assertEqual(convert_quantity("1.5", "kg", "g"), Decimal("1500.0"))

    def test_millilitres_convert_to_litres(self):
        self.assertEqual(convert_quantity("750", "ml", "litre"), Decimal("0.750"))

    def test_unit_aliases_are_case_and_whitespace_insensitive(self):
        self.assertEqual(convert_quantity("2", " KG ", "grams"), Decimal("2000"))

    def test_same_unknown_unit_can_pass_through_without_conversion(self):
        self.assertEqual(convert_quantity("3.25", "scoop", "scoop"), Decimal("3.25"))

    def test_rejects_mass_to_volume_conversion(self):
        with self.assertRaisesMessage(ValidationError, "measure different things"):
            convert_quantity("1", "kg", "litre")

    def test_rejects_unsupported_cross_unit_conversion(self):
        with self.assertRaisesMessage(ValidationError, "Unsupported unit conversion"):
            convert_quantity("1", "carton", "pcs")

    def test_rejects_non_finite_quantity(self):
        with self.assertRaises(ValidationError):
            convert_quantity("NaN", "g", "kg")

    def test_rejects_negative_quantity(self):
        with self.assertRaises(ValidationError):
            convert_quantity("-1", "g", "kg")
