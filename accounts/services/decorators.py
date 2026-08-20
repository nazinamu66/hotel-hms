from functools import wraps

from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied

from accounts.services.access import (
    user_can_access_hotel,
)


def role_required(*allowed_roles):

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):

            if not request.user.is_authenticated:
                return redirect(
                    "/accounts/login/"
                )

            if request.user.role not in allowed_roles:
                raise PermissionDenied

            return view_func(
                request,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator


def hotel_access_required(
    hotel_getter=None,
):
    """
    Require the current user to have access to a hotel.

    hotel_getter:
        Optional callable that receives request and kwargs
        and returns the Hotel instance.
    """

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):

            if not request.user.is_authenticated:
                return redirect(
                    "/accounts/login/"
                )

            if hotel_getter is None:
                hotel = getattr(
                    request.user,
                    "hotel",
                    None,
                )

            else:
                hotel = hotel_getter(
                    request,
                    *args,
                    **kwargs,
                )

            if not user_can_access_hotel(
                request.user,
                hotel,
            ):
                raise PermissionDenied

            return view_func(
                request,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator


def manager_admin_or_director(user):

    return user.is_authenticated and user.role in {
        "MANAGER",
        "ADMIN",
        "DIRECTOR",
    }