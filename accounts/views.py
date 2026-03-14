from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import logout
from accounts.decorators import role_required
from django.shortcuts import render
from django.db.models import Sum, Count
from decimal import Decimal
from inventory.models import LowStockRequest, PurchaseItem, PurchaseOrder
from django.contrib import messages
from restaurant.models import POSOrder
from billing.models import Folio, Payment
from django.utils import timezone
from core.utils import get_user_hotel
from reports.utils import today_range
from django.shortcuts import get_object_or_404
from accounts.services.manager_reports import build_manager_daily_report
from .forms import UserCreateForm
from django.contrib import messages
from django.shortcuts import redirect
from inventory.models import Department
from accounts.services.manager_reports import (
    build_manager_daily_report,
    get_today_restaurant_orders,
    get_today_room_activity,
    get_today_payments,
)



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

    if role == "DIRECTOR":
        return redirect("owner_dashboard")

    if role == "MANAGER":
        return redirect("manager_dashboard")

    if role == "ADMIN":
        return redirect("admin_dashboard")

    if role == "FRONTDESK":
        return redirect("/frontdesk/")

    if role == "RESTAURANT":
        return redirect("/restaurant/pos/")

    if role == "STORE":
        return redirect("/store/")

    if role == "KITCHEN":
        return redirect("/kitchen/")
    
    if role == "HOUSEKEEPING":
        return redirect("/housekeeping/")

    return redirect("login")




@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_restaurant_orders_today(request):

    hotel = get_user_hotel(request.user)

    orders = get_today_restaurant_orders(hotel=hotel)

    return render(
        request,
        "accounts/manager/restaurant_orders_today.html",
        {"orders": orders}
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_room_activity_today(request):

    hotel = get_user_hotel(request.user)

    activity = get_today_room_activity(hotel=hotel)

    return render(
        request,
        "accounts/manager/room_activity_today.html",
        activity
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_payments_today(request):

    hotel = get_user_hotel(request.user)

    payments = get_today_payments(hotel=hotel)

    return render(
        request,
        "accounts/manager/payments_today.html",
        {"payments": payments}
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_dashboard(request):

    user = request.user
    hotel = get_user_hotel(user)

    if user.role == "MANAGER":
        context = build_manager_daily_report(hotel=hotel)

    elif user.role in ["ADMIN", "DIRECTOR"]:
        context = build_manager_daily_report(hotel=None)  # global

    else:
        return redirect("role_redirect")

    return render(request, "accounts/manager_dashboard.html", context)


@role_required("ADMIN", "DIRECTOR")
def admin_dashboard(request):

    hotel = None

    if request.user.department:
        hotel = request.user.department.hotel

    context = {
        "hotel": hotel
    }

    return render(
        request,
        "accounts/admin_dashboard.html",
        context
    )

from django.contrib.auth import get_user_model
from django.shortcuts import render
from accounts.decorators import role_required
from core.utils import get_user_hotel

User = get_user_model()


from django.db.models import Q

from django.contrib.auth import get_user_model
from accounts.decorators import role_required

User = get_user_model()


@role_required("ADMIN", "DIRECTOR")
def user_list(request):

    users = (
        User.objects
        .select_related("department")
        .order_by("username")
    )

    return render(
        request,
        "accounts/user_list.html",
        {"users": users}
    )


@role_required("ADMIN", "DIRECTOR")
def user_create(request):

    hotel = get_user_hotel(request.user)

    form = UserCreateForm(request.POST or None)

    if request.user.role != "DIRECTOR":
        form.fields["department"].queryset = Department.objects.filter(
            hotel=hotel
        )

    if form.is_valid():
        form.save()
        messages.success(request, "User created successfully.")
        return redirect("accounts_users")

    return render(
        request,
        "accounts/user_form.html",
        {"form": form}
    )