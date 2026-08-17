from wifi.models import WiFiSession, RadiusAccount


def check_wifi_account_access(
    *,
    hotel,
    account,
    mac_address,
):
    """
    Determine whether a RadiusAccount may use a particular device.

    This does not authenticate the username/password.
    Authentication should happen first through
    authenticate_wifi_account().
    """

    # ---------------------------------------------------------
    # HOTEL ISOLATION
    # ---------------------------------------------------------

    if account.hotel_id != hotel.id:
        return {
            "allowed": False,
            "reason": "ACCOUNT_HOTEL_MISMATCH",
            "account": account,
        }

    # ---------------------------------------------------------
    # ACCOUNT STATUS
    # ---------------------------------------------------------

    if account.status == "SUSPENDED":
        return {
            "allowed": False,
            "reason": "ACCOUNT_SUSPENDED",
            "account": account,
        }

    if account.status == "DISABLED":
        return {
            "allowed": False,
            "reason": "ACCOUNT_DISABLED",
            "account": account,
        }

    if account.status == "EXPIRED":
        return {
            "allowed": False,
            "reason": "ACCOUNT_EXPIRED",
            "account": account,
        }

    if account.status != "ACTIVE":
        return {
            "allowed": False,
            "reason": "ACCOUNT_INACTIVE",
            "account": account,
        }

    # ---------------------------------------------------------
    # ACTIVE SESSIONS
    # ---------------------------------------------------------

    active_sessions = WiFiSession.objects.filter(
        hotel=hotel,
        radius_account=account,
        status="ACTIVE",
    )

    # ---------------------------------------------------------
    # EXISTING DEVICE
    # ---------------------------------------------------------

    existing_session = active_sessions.filter(
        mac_address=mac_address,
    ).exists()

    if existing_session:
        return {
            "allowed": True,
            "reason": "VALID_ACCOUNT",
            "account": account,
            "profile": account.profile,
        }

    # ---------------------------------------------------------
    # DEVICE LIMIT
    # ---------------------------------------------------------

    active_device_count = (
        active_sessions
        .values("mac_address")
        .distinct()
        .count()
    )

    if active_device_count >= account.max_devices:
        return {
            "allowed": False,
            "reason": "DEVICE_LIMIT_REACHED",
            "account": account,
        }

    # ---------------------------------------------------------
    # AVAILABLE DEVICE SLOT
    # ---------------------------------------------------------

    return {
        "allowed": True,
        "reason": "VALID_ACCOUNT",
        "account": account,
        "profile": account.profile,
    }