from django.utils import timezone

from wifi.models import RadiusAccount


def authenticate_wifi_account(
    *,
    hotel,
    username,
    password,
):
    """
    Authenticate a guest Wi-Fi account.

    This validates the ERP-level account credentials
    and account validity period.

    It does not create a Wi-Fi session and does not
    enforce the physical network connection.
    """

    account = (
        RadiusAccount.objects
        .select_related("profile", "guest")
        .filter(
            hotel=hotel,
            username=username,
        )
        .first()
    )

    # ---------------------------------------------------------
    # ACCOUNT DOES NOT EXIST
    # ---------------------------------------------------------

    if account is None:
        return {
            "authenticated": False,
            "reason": "INVALID_CREDENTIALS",
        }

    # ---------------------------------------------------------
    # ACCOUNT STATUS
    # ---------------------------------------------------------

    if account.status == "SUSPENDED":
        return {
            "authenticated": False,
            "reason": "ACCOUNT_SUSPENDED",
            "account": account,
        }

    if account.status == "DISABLED":
        return {
            "authenticated": False,
            "reason": "ACCOUNT_DISABLED",
            "account": account,
        }

    if account.status == "EXPIRED":
        return {
            "authenticated": False,
            "reason": "ACCOUNT_EXPIRED",
            "account": account,
        }

    if account.status != "ACTIVE":
        return {
            "authenticated": False,
            "reason": "ACCOUNT_INACTIVE",
            "account": account,
        }

    # ---------------------------------------------------------
    # VALIDITY PERIOD
    # ---------------------------------------------------------

    now = timezone.now()

    if now < account.valid_from:
        return {
            "authenticated": False,
            "reason": "ACCOUNT_NOT_YET_VALID",
            "account": account,
        }

    if now >= account.valid_until:
        return {
            "authenticated": False,
            "reason": "ACCOUNT_EXPIRED",
            "account": account,
        }

    # ---------------------------------------------------------
    # PASSWORD
    # ---------------------------------------------------------

    if account.password != password:
        return {
            "authenticated": False,
            "reason": "INVALID_CREDENTIALS",
        }

    # ---------------------------------------------------------
    # SUCCESS
    # ---------------------------------------------------------

    return {
        "authenticated": True,
        "reason": "VALID_ACCOUNT",
        "account": account,
        "profile": account.profile,
    }