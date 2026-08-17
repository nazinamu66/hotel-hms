from django.utils import timezone

from wifi.models import RadiusAccount, WiFiSession
from .provisioning import (
    expire_radius_account,
    get_wifi_backend,
)


def expire_due_accounts(hotel=None):
    """
    Expire all active Wi-Fi accounts whose validity period has ended.

    Active sessions belonging to expired accounts are also terminated.
    """

    now = timezone.now()

    accounts = RadiusAccount.objects.filter(
        status="ACTIVE",
        valid_until__lte=now,
    )

    if hotel is not None:
        accounts = accounts.filter(hotel=hotel)

    backend = get_wifi_backend()

    expired = []

    for account in accounts:

        # -------------------------------
        # Terminate active sessions
        # -------------------------------

        sessions = WiFiSession.objects.filter(
            radius_account=account,
            status="ACTIVE",
        )

        for session in sessions:

            backend.disconnect_session(session)

            session.status = "EXPIRED"
            session.ended_at = now

            session.save(
                update_fields=[
                    "status",
                    "ended_at",
                ]
            )

        # -------------------------------
        # Expire Radius account
        # -------------------------------

        expire_radius_account(account)

        expired.append(account)

    return expired