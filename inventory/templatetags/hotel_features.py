from django import template
from inventory.utils import hotel_has_feature

register = template.Library()


@register.simple_tag
def feature_enabled(user, feature):

    hotel = getattr(user, "hotel", None)

    return hotel_has_feature(hotel, feature)