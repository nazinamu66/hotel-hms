from decimal import Decimal


def get_latest_cost(product, kitchen):

    from kitchen.models import ProductionBatch

    batch = (
        ProductionBatch.objects
        .filter(
            recipe__product=product,
            is_executed=True
        )
        .order_by("-id")
        .first()
    )

    if not batch:
        return Decimal("0.00")

    return Decimal(batch.cost_per_unit())