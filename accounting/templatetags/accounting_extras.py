from decimal import Decimal
from django import template

register = template.Library()


@register.filter
def money(value):
    """
    Format a number as Nigerian Naira with thousands separators.

    Example:
        9000 -> ₦9,000.00
        500000.5 -> ₦500,000.50
    """

    if value is None:
        return "₦0.00"

    try:
        value = Decimal(str(value))
    except (TypeError, ValueError):
        return value

    return f"₦{value:,.2f}"
