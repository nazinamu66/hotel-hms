from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from accounts.decorators import role_required
from .forms import WiFiProfileForm
from .models import WiFiProfile
from datetime import timedelta
from .services.vouchers import create_wifi_voucher, revoke_wifi_voucher
from django.utils import timezone
from billing.models import Guest
from .forms import WiFiProfileForm, WiFiGuestAccountForm, WiFiDeviceForm,WiFiVoucherForm
from .models import WiFiProfile, RadiusAccount,WiFiSession,WiFiDevice,WiFiVoucher
from .services.provisioning import create_guest_wifi_account



@role_required("DIRECTOR", "ADMIN", "MANAGER")
def profile_list(request):

    hotel = request.user.hotel

    profiles = WiFiProfile.objects.filter(
        hotel=hotel
    ).order_by("name")

    return render(
        request,
        "wifi/profiles.html",
        {
            "profiles": profiles,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def profile_create(request):

    hotel = request.user.hotel

    if request.method == "POST":

        form = WiFiProfileForm(request.POST)

        if form.is_valid():

            profile = form.save(commit=False)
            profile.hotel = hotel
            profile.save()

            messages.success(
                request,
                f"Wi-Fi profile '{profile.name}' created successfully.",
            )

            return redirect("wifi:profile_list")

    else:

        form = WiFiProfileForm()

    return render(
        request,
        "wifi/profile_form.html",
        {
            "form": form,
            "page_title": "Create Wi-Fi Profile",
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def profile_edit(request, profile_id):

    hotel = request.user.hotel

    profile = get_object_or_404(
        WiFiProfile,
        id=profile_id,
        hotel=hotel,
    )

    if request.method == "POST":

        form = WiFiProfileForm(
            request.POST,
            instance=profile,
        )

        if form.is_valid():

            profile = form.save()

            messages.success(
                request,
                f"Wi-Fi profile '{profile.name}' updated successfully.",
            )

            return redirect("wifi:profile_list")

    else:

        form = WiFiProfileForm(
            instance=profile,
        )

    return render(
        request,
        "wifi/profile_form.html",
        {
            "form": form,
            "page_title": "Edit Wi-Fi Profile",
            "profile": profile,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def profile_toggle(request, profile_id):

    hotel = request.user.hotel

    profile = get_object_or_404(
        WiFiProfile,
        id=profile_id,
        hotel=hotel,
    )

    if request.method == "POST":

        profile.is_active = not profile.is_active

        profile.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        status = "activated" if profile.is_active else "deactivated"

        messages.success(
            request,
            f"Wi-Fi profile '{profile.name}' {status}.",
        )

    return redirect("wifi:profile_list")

@role_required("DIRECTOR", "ADMIN", "MANAGER")
def account_list(request):

    hotel = request.user.hotel

    accounts = (
        RadiusAccount.objects
        .filter(hotel=hotel)
        .select_related("guest", "profile")
        .order_by("-created_at")
    )

    return render(
        request,
        "wifi/accounts.html",
        {
            "accounts": accounts,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def account_create(request):

    hotel = request.user.hotel

    if request.method == "POST":

        form = WiFiGuestAccountForm(
            request.POST,
            hotel=hotel,
        )

        if form.is_valid():

            guest = form.cleaned_data["guest"]
            profile = form.cleaned_data["profile"]
            valid_from = form.cleaned_data["valid_from"]
            valid_until = form.cleaned_data["valid_until"]

            result = create_guest_wifi_account(
                hotel=hotel,
                guest=guest,
                profile=profile,
                valid_from=valid_from,
                valid_until=valid_until,
            )

            account = result["account"]

            # Store newly generated credentials temporarily
            # so the password is not exposed in the URL.
            request.session["wifi_created_account"] = {
                "account_id": account.id,
                "username": result["username"],
                "password": result["password"],
            }

            messages.success(
                request,
                f"Wi-Fi account '{account.username}' created successfully.",
            )

            return redirect(
                "wifi:account_detail",
                account_id=account.id,
            )

    else:

        now = timezone.now()

        form = WiFiGuestAccountForm(
            hotel=hotel,
            initial={
                "valid_from": now,
                "valid_until": now + timedelta(days=1),
            },
        )

    return render(
        request,
        "wifi/account_form.html",
        {
            "form": form,
            "page_title": "Generate Guest Wi-Fi Account",
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def account_detail(request, account_id):

    hotel = request.user.hotel

    account = get_object_or_404(
        RadiusAccount,
        id=account_id,
        hotel=hotel,
    )

    # Credentials are available only immediately after account creation.
    created_credentials = None

    session_credentials = request.session.pop(
        "wifi_created_account",
        None,
    )

    if (
        session_credentials
        and session_credentials.get("account_id") == account.id
    ):
        created_credentials = {
            "username": session_credentials.get("username"),
            "password": session_credentials.get("password"),
        }

    sessions = (
        WiFiSession.objects
        .filter(radius_account=account)
        .order_by("-started_at")
    )

    return render(
        request,
        "wifi/account_detail.html",
        {
            "account": account,
            "sessions": sessions,
            "created_credentials": created_credentials,
        },
    )
@role_required("DIRECTOR", "ADMIN", "MANAGER")
def device_list(request):

    hotel = request.user.hotel

    devices = (
        WiFiDevice.objects
        .filter(hotel=hotel)
        .order_by("name")
    )

    return render(
        request,
        "wifi/devices.html",
        {
            "devices": devices,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def device_create(request):

    hotel = request.user.hotel

    if request.method == "POST":

        form = WiFiDeviceForm(request.POST)

        if form.is_valid():

            device = form.save(commit=False)
            device.hotel = hotel
            device.save()

            messages.success(
                request,
                f"Wi-Fi device '{device.name}' registered successfully.",
            )

            return redirect("wifi:device_list")

    else:

        form = WiFiDeviceForm()

    return render(
        request,
        "wifi/device_form.html",
        {
            "form": form,
            "page_title": "Register Wi-Fi Device",
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def device_edit(request, device_id):

    hotel = request.user.hotel

    device = get_object_or_404(
        WiFiDevice,
        id=device_id,
        hotel=hotel,
    )

    if request.method == "POST":

        form = WiFiDeviceForm(
            request.POST,
            instance=device,
        )

        if form.is_valid():

            device = form.save()

            messages.success(
                request,
                f"Wi-Fi device '{device.name}' updated successfully.",
            )

            return redirect("wifi:device_list")

    else:

        form = WiFiDeviceForm(
            instance=device,
        )

    return render(
        request,
        "wifi/device_form.html",
        {
            "form": form,
            "page_title": "Edit Wi-Fi Device",
            "device": device,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def device_toggle(request, device_id):

    hotel = request.user.hotel

    device = get_object_or_404(
        WiFiDevice,
        id=device_id,
        hotel=hotel,
    )

    if request.method == "POST":

        device.is_active = not device.is_active

        device.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        status = "activated" if device.is_active else "deactivated"

        messages.success(
            request,
            f"Wi-Fi device '{device.name}' {status}.",
        )

    return redirect("wifi:device_list")

@role_required("DIRECTOR", "ADMIN", "MANAGER")
def voucher_list(request):

    hotel = request.user.hotel

    vouchers = (
        WiFiVoucher.objects
        .filter(hotel=hotel)
        .select_related("profile", "created_by")
        .order_by("-created_at")
    )

    return render(
        request,
        "wifi/vouchers.html",
        {
            "vouchers": vouchers,
        },
    )


@role_required("DIRECTOR", "ADMIN", "MANAGER")
def voucher_create(request):

    hotel = request.user.hotel

    if request.method == "POST":

        form = WiFiVoucherForm(
            request.POST,
            hotel=hotel,
        )

        if form.is_valid():

            voucher = create_wifi_voucher(
                hotel=hotel,
                profile=form.cleaned_data["profile"],
                valid_from=form.cleaned_data["valid_from"],
                valid_until=form.cleaned_data["valid_until"],
                max_devices=form.cleaned_data["max_devices"],
                created_by=request.user,
                notes=form.cleaned_data["notes"],
            )

            # Store the newly-created voucher ID temporarily.
            # This allows the detail page to know that this
            # voucher was just generated.
            request.session["wifi_created_voucher"] = voucher.id

            messages.success(
                request,
                f"Wi-Fi voucher '{voucher.code}' created successfully.",
            )

            return redirect(
                "wifi:voucher_detail",
                voucher_id=voucher.id,
            )

    else:

        now = timezone.now()

        form = WiFiVoucherForm(
            hotel=hotel,
            initial={
                "valid_from": now,
                "valid_until": now + timedelta(hours=4),
                "max_devices": 1,
            },
        )

    return render(
        request,
        "wifi/voucher_form.html",
        {
            "form": form,
            "page_title": "Generate Wi-Fi Voucher",
        },
    )

@role_required("DIRECTOR", "ADMIN", "MANAGER")
def voucher_detail(request, voucher_id):

    hotel = request.user.hotel

    voucher = get_object_or_404(
        WiFiVoucher.objects.select_related(
            "profile",
            "created_by",
        ),
        id=voucher_id,
        hotel=hotel,
    )

    # Only the voucher that was just created gets
    # its code highlighted as a newly generated credential.
    is_new = (
        request.session.pop(
            "wifi_created_voucher",
            None,
        )
        == voucher.id
    )

    return render(
        request,
        "wifi/voucher_detail.html",
        {
            "voucher": voucher,
            "is_new": is_new,
        },
    )

@role_required("DIRECTOR", "ADMIN", "MANAGER")
def voucher_revoke(request, voucher_id):

    hotel = request.user.hotel

    voucher = get_object_or_404(
        WiFiVoucher,
        id=voucher_id,
        hotel=hotel,
    )

    if request.method == "POST":

        if voucher.status == "REVOKED":
            messages.info(
                request,
                f"Voucher '{voucher.code}' is already revoked.",
            )

            return redirect(
                "wifi:voucher_detail",
                voucher_id=voucher.id,
            )

        result = revoke_wifi_voucher(
            voucher=voucher,
            hotel=hotel,
        )

        messages.success(
            request,
            (
                f"Voucher '{voucher.code}' revoked successfully. "
                f"{result['sessions_terminated']} active session(s) terminated."
            ),
        )

        return redirect(
            "wifi:voucher_detail",
            voucher_id=voucher.id,
        )

    return redirect(
        "wifi:voucher_detail",
        voucher_id=voucher.id,
    )