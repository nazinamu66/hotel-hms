from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.shortcuts import redirect
from django.contrib.auth import logout
from accounts.decorators import role_required
from django.shortcuts import render


class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'

    def get_success_url(self):
        return reverse_lazy('role_redirect')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def role_redirect(request):
    role = request.user.role

    redirects = {
        "ADMIN": "/accounts/admin-panel/",
        "MANAGER": "/accounts/manager/",
        "FRONTDESK": "/frontdesk/",
        "RESTAURANT": "/restaurant/pos/",
        "STORE": "/store/",
        "KITCHEN": "/kitchen/",
        "HOUSEKEEPING": "/housekeeping/",
    }

    return redirect(redirects.get(role, "/accounts/login/"))



from django.db.models import Sum, Count
from django.utils.timezone import now
from decimal import Decimal

from restaurant.models import POSOrder
from billing.models import Folio, Payment
from reports.utils import today_range
from accounts.decorators import role_required
from django.utils.timezone import now
from accounts.services.manager_reports import build_manager_daily_report
from accounts.decorators import role_required
from accounts.services.manager_reports import (
    get_today_restaurant_orders,
    get_today_room_activity,
    get_today_payments,
)

@role_required("MANAGER", "ADMIN")
def manager_restaurant_orders_today(request):
    orders = get_today_restaurant_orders()
    return render(
        request,
        "accounts/manager/restaurant_orders_today.html",
        {"orders": orders}
    )

@role_required("MANAGER", "ADMIN")
def manager_room_activity_today(request):
    activity = get_today_room_activity()
    return render(
        request,
        "accounts/manager/room_activity_today.html",
        activity
    )

@role_required("MANAGER", "ADMIN")
def manager_payments_today(request):
    payments = get_today_payments()
    return render(
        request,
        "accounts/manager/payments_today.html",
        {"payments": payments}
    )


@role_required("MANAGER", "ADMIN")
def manager_dashboard(request):
    context = build_manager_daily_report()
    return render(request, "accounts/manager_dashboard.html", context)


@role_required("ADMIN")
def admin_dashboard(request):
    return render(request, "accounts/admin_dashboard.html")


