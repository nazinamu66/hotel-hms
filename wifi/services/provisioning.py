from django.utils import timezone

from wifi.models import RadiusAccount
from .development import DevelopmentWiFiBackend
from django.db import transaction

from wifi.models import RadiusAccount
from .credentials import (
    generate_wifi_username,
    generate_wifi_password,
)


def get_wifi_backend():
    """
    Return the configured Wi-Fi authentication backend.

    Development backend for now.
    Production backend will be selected through configuration.
    """

    return DevelopmentWiFiBackend()


def provision_radius_account(account):
    """
    Provision a RadiusAccount through the configured backend.
    """

    backend = get_wifi_backend()

    result = backend.create_account(account)

    if not result.get("success"):
        raise RuntimeError(
            result.get(
                "message",
                "Wi-Fi account provisioning failed.",
            )
        )

    return result


def disable_radius_account(account):
    """
    Disable a RadiusAccount through the configured backend.
    """

    backend = get_wifi_backend()

    result = backend.disable_account(account)

    if result.get("success"):
        account.status = "DISABLED"
        account.save(update_fields=["status", "updated_at"])

    return result


def expire_radius_account(account):
    """
    Expire a RadiusAccount when its validity period ends.
    """

    backend = get_wifi_backend()

    result = backend.disable_account(account)

    if result.get("success"):
        account.status = "EXPIRED"
        account.save(update_fields=["status", "updated_at"])

    return result


def is_account_valid(account):
    """
    Determine whether a Wi-Fi account is currently valid.
    """

    now = timezone.now()

    return (
        account.status == "ACTIVE"
        and account.valid_from <= now
        and account.valid_until > now
    )

@transaction.atomic
def create_guest_wifi_account(
    *,
    hotel,
    guest,
    profile,
    valid_from,
    valid_until,
    max_devices=None,
    notes="",
):
    """
    Create and provision a Wi-Fi account for a hotel guest.
    """

    if max_devices is None:
        max_devices = profile.max_devices

    for _ in range(10):

        username = generate_wifi_username(hotel)

        if not RadiusAccount.objects.filter(
            hotel=hotel,
            username=username,
        ).exists():
            break

    else:
        raise RuntimeError(
            "Unable to generate a unique Wi-Fi username."
        )

    password = generate_wifi_password()

    account = RadiusAccount.objects.create(
        hotel=hotel,
        guest=guest,
        username=username,
        password=password,
        profile=profile,
        valid_from=valid_from,
        valid_until=valid_until,
        max_devices=max_devices,
        notes=notes,
        status="ACTIVE",
    )

    result = provision_radius_account(account)

    return {
        "account": account,
        "username": username,
        "password": password,
        "backend_result": result,
    }