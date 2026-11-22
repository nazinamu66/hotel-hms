from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services.access import user_can_access_hotel

from linen.models import (
    LinenTransaction,
    RoomLinenOpeningBatch,
    RoomLinenOpeningRoom,
    RoomLinenOpeningLine,
    RoomLinenRequirement,
    RoomLinenSetup,
    RoomLinenState,
)
from rooms.models import Room


@transaction.atomic
def create_room_linen_opening_batch(*, hotel, user, reference, notes=""):
    """Snapshot category defaults for every room not yet initialized."""
    if not user_can_access_hotel(user, hotel):
        raise ValidationError("You do not have access to this hotel.")
    reference = (reference or "").strip()
    if not reference:
        raise ValidationError("Opening batch reference is required.")

    rooms = list(
        Room.objects.select_for_update()
        .filter(hotel=hotel)
        .exclude(linen_setup__is_configured=True)
        .select_related("category")
        .order_by("room_number")
    )
    if not rooms:
        raise ValidationError("There are no uninitialized rooms for this hotel.")

    batch = RoomLinenOpeningBatch.objects.create(
        hotel=hotel, reference=reference, notes=notes, created_by=user,
    )
    for room in rooms:
        opening_room = RoomLinenOpeningRoom.objects.create(
            batch=batch, room=room, category_name=room.category.name,
        )
        defaults = RoomLinenRequirement.objects.filter(
            category=room.category,
            linen_item__is_active=True,
            linen_item__product__hotel=hotel,
            linen_item__product__is_active=True,
        ).select_related("linen_item")
        RoomLinenOpeningLine.objects.bulk_create([
            RoomLinenOpeningLine(
                opening_room=opening_room,
                linen_item=row.linen_item,
                expected_quantity=row.quantity,
                actual_quantity=row.quantity,
            )
            for row in defaults
        ])
    return batch


@transaction.atomic
def post_room_linen_opening_batch(*, batch, user):
    """Post confirmed physical room counts without changing department balances."""
    batch = RoomLinenOpeningBatch.objects.select_for_update().get(pk=batch.pk)
    if not user_can_access_hotel(user, batch.hotel):
        raise ValidationError("You do not have access to this hotel.")
    if batch.status != "DRAFT":
        raise ValidationError("Only a draft room linen opening batch can be posted.")

    rooms = list(
        batch.rooms.select_for_update()
        .select_related("room", "room__hotel")
        .prefetch_related("lines__linen_item")
    )
    if not rooms:
        raise ValidationError("The room opening batch contains no rooms.")

    for opening_room in rooms:
        room = Room.objects.select_for_update().get(pk=opening_room.room_id)
        setup, _ = RoomLinenSetup.objects.select_for_update().get_or_create(room=room)
        if setup.is_configured:
            raise ValidationError(
                f"Room {room.room_number} has already been initialized. "
                "It cannot be included in another opening batch."
            )

        lines = list(opening_room.lines.select_related("linen_item", "linen_item__product"))
        for line in lines:
            if line.linen_item.product.hotel_id != batch.hotel_id:
                raise ValidationError("A room opening line contains linen from another hotel.")

        # Legacy setup visits could have created default states before the
        # batch workflow existed. Replace that unposted configuration from
        # the confirmed batch snapshot, never merge stale category rows.
        RoomLinenState.objects.filter(room=room).delete()
        for line in lines:
            RoomLinenState.objects.update_or_create(
                room=room,
                linen_item=line.linen_item,
                defaults={"quantity": line.actual_quantity},
            )
            if line.actual_quantity:
                LinenTransaction.objects.create(
                    linen_item=line.linen_item,
                    quantity=line.actual_quantity,
                    condition="CLEAN",
                    event_type="ROOM_OPENING",
                    to_room=room,
                    performed_by=user,
                    reference=(
                        f"ROOM-OPENING-{batch.pk}-ROOM-{room.pk}-"
                        f"ITEM-{line.linen_item_id}"
                    ),
                    note=(
                        f"Opening physical count for Room {room.room_number}; "
                        f"batch {batch.reference}. Category baseline: "
                        f"{line.expected_quantity}."
                    ),
                )

        setup.is_configured = True
        setup.opening_batch = batch
        setup.save(update_fields=["is_configured", "opening_batch", "configured_at"])

    batch.status = "POSTED"
    batch.posted_by = user
    batch.posted_at = timezone.now()
    batch.save(update_fields=["status", "posted_by", "posted_at"])
    return batch
