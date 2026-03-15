from django import template

register = template.Library()


@register.filter
def has_role(user, roles):
    role_list = [r.strip() for r in roles.split(",")]
    return user.role in role_list