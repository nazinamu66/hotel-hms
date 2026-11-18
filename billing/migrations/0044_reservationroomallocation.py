from django.db import migrations, models
import django.db.models.deletion


def backfill_ordinary_reservation_allocations(apps, schema_editor):
    Reservation = apps.get_model("billing", "Reservation")
    GroupReservationRoom = apps.get_model("billing", "GroupReservationRoom")
    Allocation = apps.get_model("billing", "ReservationRoomAllocation")
    Folio = apps.get_model("billing", "Folio")
    db_alias = schema_editor.connection.alias

    corporate_reservation_ids = GroupReservationRoom.objects.using(
        db_alias
    ).exclude(reservation_id__isnull=True).values_list(
        "reservation_id", flat=True
    )

    reservations = Reservation.objects.using(db_alias).exclude(
        pk__in=corporate_reservation_ids
    ).order_by("pk")

    for reservation in reservations.iterator():
        room_count = max(int(reservation.number_of_rooms or 1), 1)
        linked_folios = list(
            Folio.objects.using(db_alias).filter(
                reservation_id=reservation.pk,
                folio_type="ROOM",
            ).order_by("-is_closed", "-check_in_at", "-pk")
        )

        # The legacy Reservation.room field represented the first/only room.
        # Preserve it on allocation 1. A linked folio is attached to the
        # matching room where possible, otherwise to the first allocation.
        first_room_id = reservation.room_id
        assigned_folio = None
        if linked_folios:
            if first_room_id:
                assigned_folio = next(
                    (folio for folio in linked_folios if folio.room_id == first_room_id),
                    None,
                )
            if assigned_folio is None:
                assigned_folio = linked_folios[0]
                if first_room_id is None:
                    first_room_id = assigned_folio.room_id

        if reservation.status == "CHECKED_IN":
            first_status = "CHECKED_IN" if assigned_folio and not assigned_folio.is_closed else "CHECKED_OUT"
        elif reservation.status == "CANCELLED":
            first_status = "CANCELLED"
        elif reservation.status == "NO_SHOW":
            first_status = "NO_SHOW"
        else:
            first_status = "RESERVED"

        for number in range(1, room_count + 1):
            Allocation.objects.using(db_alias).create(
                reservation_id=reservation.pk,
                allocation_number=number,
                guest_id=reservation.guest_id,
                room_id=first_room_id if number == 1 else None,
                folio_id=assigned_folio.pk if number == 1 and assigned_folio else None,
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
                status=first_status if number == 1 else (
                    "CANCELLED" if reservation.status == "CANCELLED"
                    else "NO_SHOW" if reservation.status == "NO_SHOW"
                    else "RESERVED"
                ),
            )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0043_eventorderline_recipe_version"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReservationRoomAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allocation_number", models.PositiveSmallIntegerField()),
                ("check_in_date", models.DateField()),
                ("check_out_date", models.DateField()),
                ("status", models.CharField(
                    choices=[
                        ("RESERVED", "Reserved"),
                        ("CHECKED_IN", "Checked In"),
                        ("CHECKED_OUT", "Checked Out"),
                        ("CANCELLED", "Cancelled"),
                        ("NO_SHOW", "No Show"),
                    ],
                    db_index=True,
                    default="RESERVED",
                    max_length=20,
                )),
                ("occupant_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("folio", models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="ordinary_reservation_allocation",
                    to="billing.folio",
                )),
                ("guest", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reservation_room_allocations",
                    to="billing.guest",
                )),
                ("reservation", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="room_allocations",
                    to="billing.reservation",
                )),
                ("room", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reservation_room_allocations",
                    to="rooms.room",
                )),
            ],
            options={
                "ordering": ["allocation_number", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="reservationroomallocation",
            constraint=models.UniqueConstraint(
                fields=("reservation", "allocation_number"),
                name="unique_reservation_room_allocation_number",
            ),
        ),
        migrations.RunPython(
            backfill_ordinary_reservation_allocations,
            migrations.RunPython.noop,
        ),
    ]
