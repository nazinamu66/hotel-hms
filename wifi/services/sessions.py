from django.db import transaction
from django.utils import timezone

from wifi.models import WiFiSession


@transaction.atomic
def start_wifi_session(
    *,
    hotel,
    mac_address,
    username="",
    radius_account=None,
    voucher=None,
    device=None,
    ip_address=None,
    session_id="",
):
    """
    Start a Wi-Fi session after access has been authorized.

    This function records the actual connection. It does not
    perform username/password authentication by itself.
    """

    now = timezone.now()

    # ---------------------------------------------------------
    # Prevent duplicate active sessions for the same MAC
    # ---------------------------------------------------------

    existing_session = (
        WiFiSession.objects
        .filter(
            hotel=hotel,
            mac_address=mac_address,
            status="ACTIVE",
        )
        .first()
    )

    if existing_session:
        return {
            "created": False,
            "session": existing_session,
            "reason": "SESSION_ALREADY_ACTIVE",
        }

    # ---------------------------------------------------------
    # Create session
    # ---------------------------------------------------------

    session = WiFiSession.objects.create(
        hotel=hotel,
        radius_account=radius_account,
        voucher=voucher,
        device=device,
        username=username,
        mac_address=mac_address,
        ip_address=ip_address,
        started_at=now,
        status="ACTIVE",
        session_id=session_id,
    )

    return {
        "created": True,
        "session": session,
        "reason": "SESSION_STARTED",
    }


@transaction.atomic
def end_wifi_session(
    *,
    session,
    status="CLOSED",
):
    """
    End an active Wi-Fi session.
    """

    if session.status != "ACTIVE":
        return {
            "ended": False,
            "session": session,
            "reason": "SESSION_NOT_ACTIVE",
        }

    session.status = status
    session.ended_at = timezone.now()

    session.save(
        update_fields=[
            "status",
            "ended_at",
        ]
    )

    return {
        "ended": True,
        "session": session,
        "reason": "SESSION_ENDED",
    }
