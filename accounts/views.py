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
from django.core.files.storage import default_storage
from django.urls import reverse
from core.models import BusinessProfile
from inventory.constants import DEFAULT_DEPARTMENTS
from inventory.models import Hotel, HotelFeature, Organization
from inventory.services.setup_hotel import setup_new_hotel
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from uuid import uuid4
from uuid import uuid4
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
    InitialOrganizationForm,
    InitialHotelProfileForm,
    InitialFeaturesForm,
    InitialDepartmentsForm,
    InitialAccountingForm,
    InitialSetupReviewForm,
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

    try:
        step = int(request.GET.get("step", "1"))
    except (TypeError, ValueError):
        step = 1

    if step < 1 or step > 6:
        step = 1

    setup_data = request.session.get("initial_setup", {})

    # ---------------------------------------------------------
    # STEP 1 — ORGANIZATION
    # ---------------------------------------------------------
    if step == 1:
        form = InitialOrganizationForm(
            initial=setup_data.get("organization", {})
        )

        if request.method == "POST":
            form = InitialOrganizationForm(request.POST)

            if form.is_valid():
                setup_data["organization"] = {
                    "organization_name": (
                        form.cleaned_data["organization_name"]
                    ),
                }

                request.session["initial_setup"] = setup_data
                request.session.modified = True

                return redirect(
                    f"{reverse('initial_setup')}?step=2"
                )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 1,
                "total_steps": 6,
                "setup_data": setup_data,
            },
        )

    # ---------------------------------------------------------
    # STEP 2 — HOTEL PROFILE
    # ---------------------------------------------------------
    if step == 2:
        if "organization" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=1"
            )

        hotel_data = setup_data.get("hotel", {})

        form_initial = {
            key: value
            for key, value in hotel_data.items()
            if key != "logo_temp_path"
        }

        form = InitialHotelProfileForm(
            initial=form_initial
        )

        if request.method == "POST":
            form = InitialHotelProfileForm(
                request.POST,
                request.FILES,
            )

            if form.is_valid():
                new_hotel_data = {
                    "hotel_name": form.cleaned_data["hotel_name"],
                    "address": form.cleaned_data["address"],
                    "city": form.cleaned_data["city"],
                    "state": form.cleaned_data["state"],
                    "country": form.cleaned_data["country"],
                    "phone": form.cleaned_data["phone"],
                    "email": form.cleaned_data["email"],
                    "website": form.cleaned_data["website"],
                    "tax_number": form.cleaned_data["tax_number"],
                    "currency": form.cleaned_data["currency"],
                }

                logo = form.cleaned_data.get("logo")

                if logo:
                    temp_name = (
                        f"setup_uploads/"
                        f"{uuid4().hex}_{logo.name}"
                    )

                    temp_path = default_storage.save(
                        temp_name,
                        logo,
                    )

                    old_logo = hotel_data.get(
                        "logo_temp_path"
                    )

                    if (
                        old_logo
                        and old_logo != temp_path
                        and default_storage.exists(old_logo)
                    ):
                        default_storage.delete(old_logo)

                    new_hotel_data["logo_temp_path"] = temp_path

                else:
                    old_logo = hotel_data.get(
                        "logo_temp_path"
                    )

                    if old_logo:
                        new_hotel_data["logo_temp_path"] = old_logo

                setup_data["hotel"] = new_hotel_data

                request.session["initial_setup"] = setup_data
                request.session.modified = True

                return redirect(
                    f"{reverse('initial_setup')}?step=3"
                )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 2,
                "total_steps": 6,
                "setup_data": setup_data,
            },
        )

    # ---------------------------------------------------------
    # STEP 3 — HOTEL FEATURES
    # ---------------------------------------------------------
    if step == 3:
        if "organization" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=1"
            )

        if "hotel" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=2"
            )

        default_features = [
            "FRONTDESK",
            "HOUSEKEEPING",
            "RESTAURANT",
            "KITCHEN",
            "STORE",
            "LAUNDRY",
        ]

        existing_features = setup_data.get(
            "features",
            default_features,
        )

        form = InitialFeaturesForm(
            initial={
                "features": existing_features,
            }
        )

        if request.method == "POST":
            form = InitialFeaturesForm(request.POST)

            if form.is_valid():
                setup_data["features"] = list(
                    form.cleaned_data["features"]
                )

                request.session["initial_setup"] = setup_data
                request.session.modified = True

                return redirect(
                    f"{reverse('initial_setup')}?step=4"
                )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 3,
                "total_steps": 6,
                "setup_data": setup_data,
            },
        )

    # ---------------------------------------------------------
    # STEP 4 — DEPARTMENTS
    # ---------------------------------------------------------
    if step == 4:
        if "organization" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=1"
            )

        if "hotel" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=2"
            )

        if "features" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=3"
            )

        department_names = [
            name
            for code, name, key in DEFAULT_DEPARTMENTS
        ]

        form = InitialDepartmentsForm(
            initial={
                "confirm": setup_data.get(
                    "departments_confirmed",
                    False,
                ),
            }
        )

        if request.method == "POST":
            form = InitialDepartmentsForm(request.POST)

            if form.is_valid():
                setup_data["departments_confirmed"] = True

                request.session["initial_setup"] = setup_data
                request.session.modified = True

                return redirect(
                    f"{reverse('initial_setup')}?step=5"
                )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 4,
                "total_steps": 6,
                "setup_data": setup_data,
                "departments": department_names,
            },
        )

    # ---------------------------------------------------------
    # STEP 5 — ACCOUNTING
    # ---------------------------------------------------------
    if step == 5:
        if "organization" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=1"
            )

        if "hotel" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=2"
            )

        if "features" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=3"
            )

        if not setup_data.get("departments_confirmed"):
            return redirect(
                f"{reverse('initial_setup')}?step=4"
            )

        form = InitialAccountingForm(
            initial={
                "confirm": setup_data.get(
                    "accounting_confirmed",
                    False,
                ),
            }
        )

        if request.method == "POST":
            form = InitialAccountingForm(request.POST)

            if form.is_valid():
                setup_data["accounting_confirmed"] = True

                request.session["initial_setup"] = setup_data
                request.session.modified = True

                return redirect(
                    f"{reverse('initial_setup')}?step=6"
                )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 5,
                "total_steps": 6,
                "setup_data": setup_data,
            },
        )

    # ---------------------------------------------------------
    # STEP 6 — REVIEW & CREATE
    # ---------------------------------------------------------
    if step == 6:
        if "organization" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=1"
            )

        if "hotel" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=2"
            )

        if "features" not in setup_data:
            return redirect(
                f"{reverse('initial_setup')}?step=3"
            )

        if not setup_data.get("departments_confirmed"):
            return redirect(
                f"{reverse('initial_setup')}?step=4"
            )

        if not setup_data.get("accounting_confirmed"):
            return redirect(
                f"{reverse('initial_setup')}?step=5"
            )
        
        feature_labels = dict(
            HotelFeature.FEATURE_CHOICES
        )

        selected_feature_labels = [
            feature_labels.get(feature, feature)
            for feature in setup_data.get("features", [])
        ]

        form = InitialSetupReviewForm(
            initial={
                "confirm": False,
            }
        )

        if request.method == "POST":
            form = InitialSetupReviewForm(request.POST)

            if form.is_valid():
                organization_data = setup_data["organization"]
                hotel_data = setup_data["hotel"]
                features = setup_data["features"]

                organization_name = (
                    organization_data["organization_name"].strip()
                )

                hotel_name = (
                    hotel_data["hotel_name"].strip()
                )

                if Organization.objects.filter(
                    name__iexact=organization_name
                ).exists():
                    form.add_error(
                        None,
                        "An organization with this name already exists.",
                    )
                elif Hotel.objects.filter(
                    name__iexact=hotel_name
                ).exists():
                    form.add_error(
                        None,
                        "A hotel with this name already exists.",
                    )
                else:
                    try:
                        with transaction.atomic():

                            # ---------------------------------
                            # ORGANIZATION
                            # ---------------------------------
                            organization = (
                                Organization.objects.create(
                                    name=organization_name
                                )
                            )

                            # ---------------------------------
                            # HOTEL
                            # ---------------------------------
                            hotel = Hotel.objects.create(
                                organization=organization,
                                name=hotel_name,
                                location=hotel_data["address"],
                            )

                            # ---------------------------------
                            # BUSINESS PROFILE
                            # ---------------------------------
                            business_profile = (
                                BusinessProfile.objects.create(
                                    hotel=hotel,
                                    name=hotel_name,
                                    address=hotel_data["address"],
                                    city=hotel_data["city"],
                                    state=hotel_data["state"],
                                    country=hotel_data["country"],
                                    phone=hotel_data["phone"],
                                    email=hotel_data["email"],
                                    website=hotel_data["website"],
                                    currency=hotel_data["currency"],
                                    tax_number=hotel_data["tax_number"],
                                )
                            )

                            # ---------------------------------
                            # LOGO
                            # ---------------------------------
                            logo_temp_path = hotel_data.get(
                                "logo_temp_path"
                            )

                            if logo_temp_path:
                                if not default_storage.exists(
                                    logo_temp_path
                                ):
                                    raise ValidationError(
                                        "The uploaded hotel logo could not be found. Please upload it again."
                                    )

                                with default_storage.open(
                                    logo_temp_path,
                                    "rb",
                                ) as logo_file:

                                    filename = os.path.basename(
                                        logo_temp_path
                                    )

                                    business_profile.logo.save(
                                        filename,
                                        File(logo_file),
                                        save=True,
                                    )

                            # ---------------------------------
                            # HOTEL FEATURES
                            # ---------------------------------
                            valid_features = {
                                code
                                for code, label
                                in HotelFeature.FEATURE_CHOICES
                            }

                            selected_features = [
                                feature
                                for feature in features
                                if feature in valid_features
                            ]

                            HotelFeature.objects.bulk_create(
                                [
                                    HotelFeature(
                                        hotel=hotel,
                                        feature=feature,
                                        is_active=True,
                                    )
                                    for feature in selected_features
                                ]
                            )

                            # ---------------------------------
                            # SYSTEM INITIALIZATION
                            # ---------------------------------
                            setup_new_hotel(hotel)

                            # ---------------------------------
                            # ADMIN → ORGANIZATION
                            # ---------------------------------
                            request.user.organization = organization
                            request.user.save(
                                update_fields=["organization"]
                            )

                        # -------------------------------------
                        # CLEANUP TEMPORARY LOGO
                        # -------------------------------------
                        logo_temp_path = hotel_data.get(
                            "logo_temp_path"
                        )

                        if (
                            logo_temp_path
                            and default_storage.exists(
                                logo_temp_path
                            )
                        ):
                            default_storage.delete(
                                logo_temp_path
                            )

                        # -------------------------------------
                        # CLEAR WIZARD SESSION
                        # -------------------------------------
                        request.session.pop(
                            "initial_setup",
                            None,
                        )
                        request.session.modified = True

                        messages.success(
                            request,
                            "Hotel setup completed successfully.",
                        )

                        return redirect(
                            "hotel_dashboard"
                        )

                    except ValidationError as e:
                        form.add_error(
                            None,
                            str(e),
                        )

        return render(
            request,
            "accounts/initial_setup.html",
            {
                "form": form,
                "step": 6,
                "total_steps": 6,
                "setup_data": setup_data,
                "selected_feature_labels": selected_feature_labels,
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