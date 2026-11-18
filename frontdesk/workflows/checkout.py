from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from billing.models import Folio, FolioRoomHistory


def validate_checkout(folio):

    if folio.is_closed:
        raise ValidationError(
            "Folio is already closed."
        )


def validate_balance(folio, user, reason=""):
    if folio.balance == 0:
        return False
    if user.role not in {"MANAGER", "DIRECTOR", "ADMIN"}:
        raise ValidationError(
            "Outstanding balance. Manager, Director or Admin approval required."
        )
    if not (reason or "").strip():
        raise ValidationError(
            "A reason is required to authorise checkout with an outstanding balance."
        )
    return True


def close_folio(folio):
    # Room nights are charged at check-in, through Night Audit, or when a
    # stay is extended. Checkout records the actual departure time only;
    # it must never create another room-night charge.
    folio.check_out_at = timezone.now()
    folio.is_closed = True

    folio.save(
        update_fields=[
            "check_out_at",
            "is_closed",
        ]
    )

def update_room_status(room):

    room.status = "VACANT_DIRTY"

    room.save(
        update_fields=[
            "status",
        ]
    )

@transaction.atomic
def checkout_guest(room, user, reason=""):
    from accounts.services.access import user_can_access_hotel
    if not user_can_access_hotel(user, room.hotel):
        raise ValidationError("You do not have access to this hotel's checkout workflow.")

    # Serialize checkout against another checkout or room-level operation.
    room = room.__class__.objects.select_for_update().get(pk=room.pk)
    folio = (
        Folio.objects.select_for_update()
        .filter(
            folio_type="ROOM",
            room=room,
            hotel=room.hotel,
            is_closed=False,
        )
        .first()
    )
    if folio is None:
        raise ValidationError("No active stay found.")

    validate_checkout(folio)

    # Company-billed room nights are excluded from folio.balance; only the
    # occupant-responsible balance is subject to the normal checkout rule.
    requires_authorization = validate_balance(folio, user, reason)
    if requires_authorization:
        from billing.models import FolioCheckoutAuthorization
        authorization = FolioCheckoutAuthorization(
            folio=folio,
            outstanding_balance=folio.balance,
            reason=reason.strip(),
            approved_by=user,
        )
        authorization.full_clean()
        authorization.save()

    close_folio(folio)

    close_room_history(folio)

    from billing.services.group_reservations import mark_group_allocation_checked_out
    mark_group_allocation_checked_out(folio)

    ordinary_allocation = (
        getattr(folio, "ordinary_reservation_allocation", None)
    )
    if ordinary_allocation is not None:
        ordinary_allocation.status = "CHECKED_OUT"
        ordinary_allocation.save(update_fields=["status"])
        from frontdesk.workflows.reservation_allocations import sync_ordinary_reservation_status
        sync_ordinary_reservation_status(ordinary_allocation.reservation)

    update_room_status(room)

    return folio

def close_room_history(folio):

    history = (
        folio.room_history
        .filter(
            ended_at__isnull=True,
        )
        .order_by("-assigned_at")
        .first()
    )

    if history:
        history.ended_at = folio.check_out_at

        history.save(
            update_fields=[
                "ended_at",
            ]
        )