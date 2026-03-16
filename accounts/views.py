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

from django.contrib.auth import get_user_model
User = get_user_model()


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

    # Executive / management dashboard
    if role in ["DIRECTOR", "MANAGER", "ADMIN", "ACCOUNTANT"]:
        return redirect("hotel_dashboard")

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

    # Restrict department selection for non-directors
    if request.user.role != "DIRECTOR":
        form.fields["department"].queryset = Department.objects.filter(
            hotel=hotel
        )

    if form.is_valid():

        user = form.save(commit=False)

        # Ensure hotel is inherited from department
        if user.department:
            user.hotel = user.department.hotel

        user.save()

        messages.success(request, "User created successfully.")

        return redirect("accounts_users")

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
        }
    )

@role_required("ADMIN", "DIRECTOR")
def user_edit(request, user_id):

    user_obj = get_object_or_404(User, id=user_id)

    form = UserCreateForm(
        request.POST or None,
        instance=user_obj
    )

    if form.is_valid():

        user = form.save(commit=False)

        if user.department:
            user.hotel = user.department.hotel

        user.save()

        messages.success(request, "User updated successfully.")

        return redirect("accounts_users")

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "edit_mode": True
        }
    )