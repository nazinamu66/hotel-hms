from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import logout
from accounts.decorators import role_required
from django.shortcuts import render
from django.contrib import messages
from core.utils import get_user_hotels
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from .forms import UserCreateForm
from django.shortcuts import redirect
from accounts.services.manager_reports import (
    build_manager_daily_report,
    get_today_restaurant_orders,
    get_today_room_activity,
    get_today_payments,
)

from django.contrib.auth import get_user_model
User = get_user_model()

from django.core.exceptions import ValidationError

from .forms import (
    UserCreateForm,
    InitialOrganizationSetupForm,
)

from accounts.services.organization import (
    create_organization_for_admin,
)


class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'

    def get_success_url(self):
        return reverse_lazy('role_redirect')


def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def initial_setup(request):

    if request.user.role != "ADMIN":
        raise PermissionDenied

    if request.user.organization_id:
        return redirect("hotel_dashboard")

    form = InitialOrganizationSetupForm(
        request.POST or None,
    )

    if request.method == "POST" and form.is_valid():

        try:

            create_organization_for_admin(
                admin=request.user,
                organization_name=form.cleaned_data[
                    "organization_name"
                ],
                hotel_name=form.cleaned_data[
                    "hotel_name"
                ],
                hotel_location=form.cleaned_data[
                    "hotel_location"
                ],
            )

        except ValidationError as e:

            form.add_error(
                None,
                e.message,
            )

        else:

            messages.success(
                request,
                "Organization and first hotel created successfully.",
            )

            return redirect(
                "hotel_dashboard",
            )

    return render(
        request,
        "accounts/initial_setup.html",
        {
            "form": form,
        },
    )


@login_required
def role_redirect(request):

    role = request.user.role

    # Executive / management dashboard
    if role == "ADMIN":

        if not request.user.organization_id:
            return redirect("initial_setup")

        return redirect("hotel_dashboard")

    if role in ["DIRECTOR", "MANAGER", "ACCOUNTANT"]:
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
    
    if role == "MAINTENANCE":
        return redirect("/maintenance/")
    
    if role == "LAUNDRY":
        return redirect("/laundry/")

    return redirect("login")



@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_restaurant_orders_today(request):

    hotel = get_user_hotels(request.user)

    orders = get_today_restaurant_orders(hotel=hotel)

    return render(
        request,
        "accounts/manager/restaurant_orders_today.html",
        {"orders": orders}
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_room_activity_today(request):

    hotel = get_user_hotels(request.user)

    activity = get_today_room_activity(hotel=hotel)

    return render(
        request,
        "accounts/manager/room_activity_today.html",
        activity
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_payments_today(request):

    hotel = get_user_hotels(request.user)

    payments = get_today_payments(hotel=hotel)

    return render(
        request,
        "accounts/manager/payments_today.html",
        {"payments": payments}
    )

def get_manageable_users(actor):

    if actor.role == "ADMIN":
        return User.objects.all()

    if actor.role == "DIRECTOR":
        return User.objects.filter(
            organization_id=actor.organization_id,
        )

    return User.objects.none()


@role_required("ADMIN", "DIRECTOR")
def user_list(request):

    users = (
        get_manageable_users(request.user)
        .select_related(
            "organization",
            "hotel",
            "department",
        )
        .prefetch_related(
            "assigned_hotels",
        )
        .order_by("username")
    )

    return render(
        request,
        "accounts/user_list.html",
        {"users": users}
    )


@role_required("ADMIN", "DIRECTOR")
def user_create(request):

    form = UserCreateForm(
        request.POST or None,
        actor=request.user,
    )

    if form.is_valid():

        try:

            user = form.save()

        except ValidationError as e:

            form.add_error(
                None,
                e.message,
            )

        else:

            messages.success(
                request,
                "User created successfully.",
            )

            return redirect(
                "accounts_users"
            )

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
        }
    )

@role_required("ADMIN", "DIRECTOR")
def user_edit(request, user_id):

    user_obj = get_object_or_404(
        get_manageable_users(request.user),
        id=user_id,
    )

    form = UserCreateForm(
        request.POST or None,
        instance=user_obj,
        actor=request.user,
    )

    if form.is_valid():

        try:

            user = form.save()

        except ValidationError as e:

            form.add_error(
                None,
                e.message,
            )

        else:

            messages.success(
                request,
                "User updated successfully.",
            )

            return redirect(
                "accounts_users"
            )

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "edit_mode": True,
        }
    )