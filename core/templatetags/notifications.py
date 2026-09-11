from django import template

from core.services.notifications.pending import get_pending_actions


register = template.Library()


@register.simple_tag
def get_pending_actions_for(user):
    return get_pending_actions(user)
