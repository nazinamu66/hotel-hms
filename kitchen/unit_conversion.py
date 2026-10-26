"""Safe unit conversion for recipe quantities and inventory base units."""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError

_UNIT_DEFINITIONS = {
    "mg": ("mass", Decimal("0.001")), "milligram": ("mass", Decimal("0.001")),
    "milligrams": ("mass", Decimal("0.001")),
    "g": ("mass", Decimal("1")), "gram": ("mass", Decimal("1")), "grams": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")), "kilogram": ("mass", Decimal("1000")), "kilograms": ("mass", Decimal("1000")),
    "ml": ("volume", Decimal("1")), "millilitre": ("volume", Decimal("1")), "millilitres": ("volume", Decimal("1")),
    "milliliter": ("volume", Decimal("1")), "milliliters": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")), "litre": ("volume", Decimal("1000")), "litres": ("volume", Decimal("1000")),
    "liter": ("volume", Decimal("1000")), "liters": ("volume", Decimal("1000")),
    "cl": ("volume", Decimal("10")), "centilitre": ("volume", Decimal("10")), "centilitres": ("volume", Decimal("10")),
    "pcs": ("count", Decimal("1")), "pc": ("count", Decimal("1")), "piece": ("count", Decimal("1")),
    "pieces": ("count", Decimal("1")), "each": ("count", Decimal("1")), "unit": ("count", Decimal("1")), "units": ("count", Decimal("1")),
}

def normalize_unit(unit):
    return " ".join(str(unit or "").strip().lower().replace(".", "").split())

def convert_quantity(quantity, from_unit, to_unit):
    """Convert a quantity between compatible units using Decimal arithmetic."""
    try:
        value = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid quantity for unit conversion.")
    if not value.is_finite() or value < 0:
        raise ValidationError("Quantity for unit conversion must be finite and non-negative.")
    source, target = normalize_unit(from_unit), normalize_unit(to_unit)
    if not source or not target:
        raise ValidationError("Both source and inventory base units must be configured.")
    if source == target:
        return value
    source_def, target_def = _UNIT_DEFINITIONS.get(source), _UNIT_DEFINITIONS.get(target)
    if not source_def or not target_def:
        raise ValidationError(f"Unsupported unit conversion: '{from_unit}' to '{to_unit}'.")
    source_dimension, source_factor = source_def
    target_dimension, target_factor = target_def
    if source_dimension != target_dimension:
        raise ValidationError(f"Cannot convert '{from_unit}' to '{to_unit}': the units measure different things.")
    return value * source_factor / target_factor
