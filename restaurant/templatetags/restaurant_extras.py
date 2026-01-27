from django import template
from decimal import Decimal

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def mul(value, arg):
    """
    Multiply two numbers safely in templates
    """
    try:
        return Decimal(value) * Decimal(arg)
    except Exception:
        return ""
