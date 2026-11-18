from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from inventory.models import Stock, StockCostLayer
from kitchen.models import ProductionBatch


ZERO = Decimal("0.000")


def build_event_ingredient_plan(event, kitchen):
    """
    Build a live ingredient requirement summary from each event line's
    approved recipe snapshot. Executed production is deducted from the
    outstanding portion requirement; physical stock and FIFO coverage
    are reported separately.
    """
    if event.hotel_id != kitchen.hotel_id or kitchen.department_type != "KITCHEN":
        raise ValidationError("The event and Kitchen department must belong to the same hotel.")

    totals = defaultdict(lambda: {
        "ingredient": None,
        "required_quantity": ZERO,
        "physical_available": ZERO,
        "fifo_available": ZERO,
        "event_lines": [],
    })

    lines = (
        event.order_lines.filter(line_type="FOOD", recipe__isnull=False)
        .select_related("recipe", "recipe_version")
        .order_by("id")
    )

    for line in lines:
        recipe = line.recipe
        version = line.recipe_version
        if not recipe.is_active or not version or version.recipe_id != recipe.pk:
            raise ValidationError(
                f"{line.description}: the event's approved recipe version snapshot is missing or invalid."
            )

        produced = (
            ProductionBatch.objects.filter(
                event_order_line=line,
                is_executed=True,
            ).aggregate(total=Sum("quantity_produced"))["total"]
            or 0
        )
        outstanding_portions = max(
            Decimal(line.quantity) - Decimal(produced),
            ZERO,
        )
        if outstanding_portions <= 0:
            continue

        for recipe_item in version.items.select_related("ingredient"):
            ingredient = recipe_item.ingredient
            if ingredient.hotel_id != event.hotel_id:
                raise ValidationError(
                    f"{ingredient.name} belongs to another hotel."
                )
            row = totals[ingredient.pk]
            row["ingredient"] = ingredient
            row["required_quantity"] += (
                Decimal(recipe_item.quantity) * outstanding_portions
            )
            row["event_lines"].append({
                "description": line.description,
                "portions": outstanding_portions,
            })

    stock_rows = Stock.objects.filter(
        department=kitchen,
        product_id__in=totals.keys(),
    ).values_list("product_id", "quantity")
    physical = {product_id: Decimal(quantity) for product_id, quantity in stock_rows}

    fifo_rows = StockCostLayer.objects.filter(
        department=kitchen,
        product_id__in=totals.keys(),
        quantity_remaining__gt=0,
    ).values_list("product_id", "quantity_remaining")
    fifo = defaultdict(lambda: ZERO)
    for product_id, quantity in fifo_rows:
        fifo[product_id] += Decimal(quantity)

    result = []
    for product_id, row in totals.items():
        row["physical_available"] = physical.get(product_id, ZERO)
        row["fifo_available"] = fifo[product_id]
        row["physical_shortage"] = max(
            row["required_quantity"] - row["physical_available"], ZERO
        )
        row["fifo_shortage"] = max(
            row["required_quantity"] - row["fifo_available"], ZERO
        )
        result.append(row)

    return sorted(result, key=lambda row: row["ingredient"].name.lower())
