from django.utils import timezone

from wifi.models import WiFiVoucher, WiFiSession
from wifi.services.devices import is_company_device_exempt


def check_wifi_access(
    *,
    hotel,
    mac_address,
    voucher_code=None,
):
    """
    Determine the ERP-level Wi-Fi access policy for a device.

    This does not authenticate the device itself.
    Authentication/enforcement remains the responsibility
    of the configured Wi-Fi backend.

    Access order:

    1. Company device exemption
    2. Valid Wi-Fi voucher
    3. Authentication required
    """

    # ---------------------------------------------------------
    # 1. COMPANY DEVICE EXEMPTION
    # ---------------------------------------------------------

    if is_company_device_exempt(
        hotel=hotel,
        mac_address=mac_address,
    ):
        return {
            "allowed": True,
            "reason": "COMPANY_DEVICE_EXEMPT",
            "requires_account": False,
        }

    # ---------------------------------------------------------
    # 2. VOUCHER ACCESS
    # ---------------------------------------------------------

    if voucher_code:

        voucher = (
            WiFiVoucher.objects
            .select_related("profile")
            .filter(
                hotel=hotel,
                code=voucher_code,
            )
            .first()
        )

        # Voucher does not exist for this hotel
        if voucher is None:
            return {
                "allowed": False,
                "reason": "INVALID_VOUCHER",
                "requires_account": True,
            }

        now = timezone.now()

        # -----------------------------------------------------
        # Voucher status checks
        # -----------------------------------------------------

        if voucher.status == "REVOKED":
            return {
                "allowed": False,
                "reason": "VOUCHER_REVOKED",
                "requires_account": True,
                "voucher": voucher,
            }

        if voucher.status == "USED":
            return {
                "allowed": False,
                "reason": "VOUCHER_USED",
                "requires_account": True,
                "voucher": voucher,
            }

        if voucher.status == "EXPIRED":
            return {
                "allowed": False,
                "reason": "VOUCHER_EXPIRED",
                "requires_account": True,
                "voucher": voucher,
            }

        if voucher.status != "ACTIVE":
            return {
                "allowed": False,
                "reason": "VOUCHER_INACTIVE",
                "requires_account": True,
                "voucher": voucher,
            }

        # -----------------------------------------------------
        # Voucher validity period
        # -----------------------------------------------------

        if now < voucher.valid_from:
            return {
                "allowed": False,
                "reason": "VOUCHER_NOT_YET_VALID",
                "requires_account": True,
                "voucher": voucher,
            }

        if now >= voucher.valid_until:
            return {
                "allowed": False,
                "reason": "VOUCHER_EXPIRED",
                "requires_account": True,
                "voucher": voucher,
            }

        # -----------------------------------------------------
        # Maximum device enforcement
        # -----------------------------------------------------

        active_sessions = WiFiSession.objects.filter(
            hotel=hotel,
            voucher=voucher,
            status="ACTIVE",
        )

        # If this MAC already has an active session,
        # allow it without consuming another device slot.
        existing_session = active_sessions.filter(
            mac_address=mac_address,
        ).exists()

        if existing_session:
            return {
                "allowed": True,
                "reason": "VALID_VOUCHER",
                "requires_account": False,
                "voucher": voucher,
            }

        active_device_count = (
            active_sessions
            .values("mac_address")
            .distinct()
            .count()
        )

        if active_device_count >= voucher.max_devices:
            return {
                "allowed": False,
                "reason": "DEVICE_LIMIT_REACHED",
                "requires_account": False,
                "voucher": voucher,
            }

        # -----------------------------------------------------
        # Voucher is valid and has an available device slot
        # -----------------------------------------------------

        return {
            "allowed": True,
            "reason": "VALID_VOUCHER",
            "requires_account": False,
            "voucher": voucher,
        }

    # ---------------------------------------------------------
    # 3. NO EXEMPTION / NO VOUCHER
    # ---------------------------------------------------------

    return {
        "allowed": False,
        "reason": "AUTHENTICATION_REQUIRED",
        "requires_account": True,
    }