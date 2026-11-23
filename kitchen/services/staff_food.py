from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Product, Stock, stock_out
from accounting.services.postings.staff_food import post_staff_food_consumption
from kitchen.models import StaffFoodIngredientUsage, StaffFoodProduction
from kitchen.unit_conversion import convert_quantity


MONEY = Decimal("0.01")


def _decimal_quantity(value, ingredient):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"Enter a valid quantity for {ingredient.name}.")
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError(f"Quantity for {ingredient.name} must be greater than zero.")
    if quantity.as_tuple().exponent < -3:
        raise ValidationError(f"Quantity for {ingredient.name} supports at most three decimal places.")
    return quantity


@transaction.atomic
def execute_staff_food_production(*, production, ingredient_entries, user):
    """
    Consume Kitchen ingredients for internal staff meals.

    Unlike guest production, this workflow does not create finished-food
    stock or a finished-goods cost layer. It expenses the actual FIFO cost
    directly to Staff Meals Expense.
    """
    production = (
        StaffFoodProduction.objects.select_for_update()
        .select_related("department__hotel", "recipe", "recipe_version")
        .get(pk=production.pk)
    )
    if production.status != "PREPARED":
        if ingredient_entries:
            raise ValidationError("A no-meal record cannot contain ingredient usage.")
        production.total_cost = Decimal("0.00")
        production.save(update_fields=["total_cost"])
        return production

    department = production.department
    if user.role != "KITCHEN" or not user.department_id or user.department_id != department.pk:
        raise ValidationError("Staff food must be recorded by staff assigned to this Kitchen.")
    if department.department_type != "KITCHEN":
        raise ValidationError("Staff food must be recorded against a Kitchen department.")
    if production.portions_prepared <= 0:
        raise ValidationError("Enter the number of portions prepared.")
    if production.portions_served > production.portions_prepared:
        raise ValidationError("Portions served cannot exceed portions prepared.")
    if production.ingredient_usages.exists():
        raise ValidationError("This staff food production has already consumed ingredients.")

    expected_items = {}
    if production.recipe_id:
        version = production.recipe_version
        if not production.recipe.is_active:
            raise ValidationError("The selected recipe is inactive.")
        if not version or version.recipe_id != production.recipe_id or version.status != "APPROVED":
            raise ValidationError("Select a recipe with a currently approved version.")
        if not version.items.exists():
            raise ValidationError("The approved recipe has no ingredients.")
        if production.recipe.product.hotel_id != department.hotel_id:
            raise ValidationError("The selected recipe belongs to another hotel.")
        for item in version.items.select_related("ingredient"):
            expected_items[item.ingredient_id] = item

    submitted = {}
    for row in ingredient_entries:
        ingredient_id = row.get("ingredient_id")
        if not ingredient_id or ingredient_id in submitted:
            raise ValidationError("Select each ingredient only once.")
        submitted[ingredient_id] = row

    if not submitted:
        raise ValidationError("Add at least one ingredient used in the staff meal.")
    if production.recipe_id and set(submitted) != set(expected_items):
        raise ValidationError("The ingredient list must match the approved recipe version.")

    validated = []
    for ingredient_id, row in submitted.items():
        try:
            ingredient = Product.objects.get(
                pk=ingredient_id,
                hotel_id=department.hotel_id,
                product_type="RAW",
                is_active=True,
            )
        except Product.DoesNotExist:
            raise ValidationError("One of the selected ingredients is unavailable for this hotel.")
        if not ingredient.departments.filter(pk=department.pk).exists():
            raise ValidationError(f"{ingredient.name} is not assigned to this Kitchen.")
        actual = _decimal_quantity(row.get("actual_quantity"), ingredient)
        expected = Decimal("0.000")
        if production.recipe_id:
            item = expected_items[ingredient.pk]
            recipe_unit = item.unit or ingredient.base_unit
            # Recipe mode trusts the immutable approved snapshot for units.
            actual = convert_quantity(actual, recipe_unit, ingredient.base_unit)
            expected = convert_quantity(
                Decimal(item.quantity) * Decimal(production.portions_prepared),
                recipe_unit,
                ingredient.base_unit,
            )
            variance = actual - expected
            variance_percent = abs(variance) / expected * Decimal("100") if expected else Decimal("0")
            absolute_limit = getattr(item, "tolerance_quantity", None)
            absolute_exceeded = absolute_limit is not None and abs(variance) > Decimal(absolute_limit)
            percentage_exceeded = variance_percent > Decimal(item.tolerance_percent)
            if item.control_mode == "STRICT" and variance != 0:
                raise ValidationError(f"{ingredient.name}: approved recipe requires exactly {expected} {ingredient.base_unit}.")
            if item.control_mode == "TOLERANCE" and (percentage_exceeded or absolute_exceeded):
                raise ValidationError(f"{ingredient.name}: actual usage exceeds the approved recipe tolerance.")
            if item.control_mode == "FLEXIBLE" and (percentage_exceeded or absolute_exceeded) and not str(row.get("variance_reason", "")).strip():
                raise ValidationError(f"Provide a variance reason for {ingredient.name}.")
        else:
            submitted_unit = str(row.get("unit") or ingredient.base_unit)
            actual = convert_quantity(actual, submitted_unit, ingredient.base_unit)
        stock = Stock.objects.select_for_update().filter(
            product=ingredient, department=department
        ).first()
        available = stock.quantity if stock else Decimal("0")
        if actual > available:
            raise ValidationError(
                f"Insufficient {ingredient.name} in Kitchen stock: need {actual}, available {available}."
            )
        validated.append((ingredient, expected, actual))

    total_cost = Decimal("0.00")
    for ingredient, expected, actual in validated:
        result = stock_out(
            product=ingredient,
            department=department,
            quantity=actual,
            user=user,
            reason=f"Staff food: {production.meal_name or 'Daily staff meal'}",
            reference=f"STAFF-FOOD-{production.pk}-{ingredient.pk}",
            allow_negative=False,
            allow_uncosted_shortage=False,
        )
        if Decimal(str(result.get("uncosted_quantity", "0"))) > 0:
            raise ValidationError(
                f"{ingredient.name} has stock without FIFO cost coverage. Reconcile its cost layers before recording staff food."
            )
        cost = Decimal(str(result["total_cost"])).quantize(MONEY, rounding=ROUND_HALF_UP)
        if cost <= 0:
            raise ValidationError(
                f"{ingredient.name} has no positive FIFO cost. Reconcile its stock valuation before recording staff food."
            )
        StaffFoodIngredientUsage.objects.create(
            production=production,
            ingredient=ingredient,
            expected_quantity=expected,
            actual_quantity=actual,
            total_cost=cost,
            stock_movement=result["movement"],
        )
        total_cost += cost

    total_cost = total_cost.quantize(MONEY, rounding=ROUND_HALF_UP)
    post_staff_food_consumption(
        production=production,
        amount=total_cost,
        user=user,
    )
    production.total_cost = total_cost
    production.save(update_fields=["total_cost"])
    return production
