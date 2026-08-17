from wifi.services.access import check_wifi_access
from wifi.services.accounts import authenticate_wifi_account
from wifi.services.account_access import check_wifi_account_access
from wifi.services.sessions import start_wifi_session


def connect_wifi_guest(
    *,
    hotel,
    mac_address,
    username=None,
    password=None,
    voucher_code=None,
    ip_address=None,
    session_id="",
    device=None,
):
    """
    Authenticate and authorize a Wi-Fi connection, then
    create a WiFiSession if access is allowed.

    Supported access methods:

    1. Company device exemption
    2. Wi-Fi voucher
    3. Guest RadiusAccount

    The actual physical Wi-Fi authentication/enforcement
    remains the responsibility of the configured backend.
    """

    # ---------------------------------------------------------
    # 1. COMPANY DEVICE / VOUCHER ACCESS
    # ---------------------------------------------------------

    # If a voucher was supplied, let the central access
    # service validate it.
    if voucher_code:

        access_result = check_wifi_access(
            hotel=hotel,
            mac_address=mac_address,
            voucher_code=voucher_code,
        )

        if not access_result["allowed"]:
            return access_result

        voucher = access_result.get("voucher")

        session_result = start_wifi_session(
            hotel=hotel,
            mac_address=mac_address,
            username=voucher.code,
            voucher=voucher,
            device=device,
            ip_address=ip_address,
            session_id=session_id,
        )

        return {
            **access_result,
            "session": session_result["session"],
            "session_result": session_result,
        }

    # ---------------------------------------------------------
    # 2. COMPANY DEVICE EXEMPTION
    # ---------------------------------------------------------

    access_result = check_wifi_access(
        hotel=hotel,
        mac_address=mac_address,
    )

    if access_result["allowed"]:

        session_result = start_wifi_session(
            hotel=hotel,
            mac_address=mac_address,
            username="",
            device=device,
            ip_address=ip_address,
            session_id=session_id,
        )

        return {
            **access_result,
            "session": session_result["session"],
            "session_result": session_result,
        }

    # ---------------------------------------------------------
    # 3. GUEST ACCOUNT AUTHENTICATION
    # ---------------------------------------------------------

    if username and password:

        authentication_result = authenticate_wifi_account(
            hotel=hotel,
            username=username,
            password=password,
        )

        if not authentication_result["authenticated"]:
            return authentication_result

        account = authentication_result["account"]

        account_access = check_wifi_account_access(
            hotel=hotel,
            account=account,
            mac_address=mac_address,
        )

        if not account_access["allowed"]:
            return account_access

        session_result = start_wifi_session(
            hotel=hotel,
            mac_address=mac_address,
            username=account.username,
            radius_account=account,
            device=device,
            ip_address=ip_address,
            session_id=session_id,
        )

        return {
            **account_access,
            "authentication": authentication_result,
            "session": session_result["session"],
            "session_result": session_result,
        }

    # ---------------------------------------------------------
    # 4. NOTHING AUTHORIZED
    # ---------------------------------------------------------

    return {
        "allowed": False,
        "reason": "AUTHENTICATION_REQUIRED",
        "requires_account": True,
    }