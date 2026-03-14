from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied


def role_required(*allowed_roles):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('/accounts/login/')

            if request.user.role not in allowed_roles:
                raise PermissionDenied  # 403

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

def manager_admin_or_director(user):
    return user.is_authenticated and user.role in [
        "MANAGER",
        "ADMIN",
        "DIRECTOR",
    ]
