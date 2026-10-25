from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def role_required(*allowed_roles):
    """
    Restrict a view to the listed application roles.

    Use with scoped querysets as well: a role check does not grant
    access to every hotel's records.
    """
    allowed = frozenset(allowed_roles)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("/accounts/login/")

            if not request.user.is_active or request.user.role not in allowed:
                raise PermissionDenied

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def manager_admin_or_director(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.role in {"MANAGER", "ADMIN", "DIRECTOR"}
    )
