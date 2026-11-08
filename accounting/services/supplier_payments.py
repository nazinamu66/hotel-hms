from decimal import Decimal
from django.db.models import DecimalField, F, Sum

from inventory.models import PurchaseReceiptItem


def purchase_order_received_total(purchase_order):
    """
    Return the supplier liability recognized for a PO, based only on
    quantities physically received and their receipt-time unit-cost snapshots.
    """
    result = PurchaseReceiptItem.objects.filter(
        receipt__purchase_order_id=purchase_order.pk,
    ).aggregate(
        total=Sum(
            F("quantity") * F("unit_cost"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )
    return result["total"] or Decimal("0.00")
