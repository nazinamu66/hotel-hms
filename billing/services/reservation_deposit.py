from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.services.journal import record_transaction

from billing.models import (
    GuestDeposit,
    Reservation,
    ReservationDeposit,
    GuestDepositTransaction,

)


GUARANTEE_PERCENTAGE = Decimal("0.20")


def calculate_reservation_value(
    nightly_rate,
    number_of_rooms,
    number_of_nights,
):
    if nightly_rate is None or nightly_rate <= 0:
        raise ValidationError(
            "A valid nightly rate is required."
        )

    if number_of_rooms < 1:
        raise ValidationError(
            "Number of rooms must be at least 1."
        )

    if number_of_nights < 1:
        raise ValidationError(
            "Number of nights must be at least 1."
        )

    return (
        nightly_rate
        * number_of_rooms
        * number_of_nights
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_DOWN,
    )


def calculate_required_guarantee(
    reservation_value,
):
    if reservation_value <= 0:
        raise ValidationError(
            "Reservation value must be greater than zero."
        )

    return (
        reservation_value
        * GUARANTEE_PERCENTAGE
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_DOWN,
    )


def calculate_cancellation_deadline(
    check_in_date,
):
    """
    Hotel reservation check-in boundary:
        12:00 PM local hotel time.

    Cancellation deadline:
        24 hours before check-in.
    """

    check_in_datetime = timezone.make_aware(
        timezone.datetime.combine(
            check_in_date,
            timezone.datetime.min.time(),
        )
    ).replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
    )

    return check_in_datetime - timezone.timedelta(
        hours=24
    )


def get_active_reservation_allocations(
    guest_deposit,
    exclude_reservation_deposit_id=None,
):
    allocations = (
        ReservationDeposit.objects
        .filter(
            guest_deposit=guest_deposit,
            status="GUARANTEED",
        )
    )

    if exclude_reservation_deposit_id:
        allocations = allocations.exclude(
            id=exclude_reservation_deposit_id
        )

    total = (
        allocations.aggregate_total
        if hasattr(allocations, "aggregate_total")
        else None
    )

    if total is not None:
        return total

    from django.db.models import Sum

    total = allocations.aggregate(
        total=Sum("allocated_amount")
    )["total"]

    return total or Decimal("0.00")

@transaction.atomic
def allocate_pending_reservation_guarantees(
    guest,
    hotel,
    user,
):
    """
    Allocate newly available guest deposit funds to pending
    reservations in oldest-first order.

    This only earmarks existing GuestDeposit.balance.
    It does not move money or create accounting entries.
    """

    guest_deposit = (
        GuestDeposit.objects
        .select_for_update()
        .filter(
            guest=guest,
            hotel=hotel,
        )
        .first()
    )

    if not guest_deposit:
        return []

    from django.db.models import Sum

    pending_reservations = (
        Reservation.objects
        .filter(
            guest=guest,
            hotel=hotel,
            status="RESERVED",
            reservation_deposit__status="PENDING",
        )
        .select_related(
            "room_category",
            "reservation_deposit",
        )
        .order_by(
            "created_at",
            "id",
        )
    )

    guaranteed = []

    for reservation in pending_reservations:

        allocated_elsewhere = (
            ReservationDeposit.objects
            .filter(
                guest_deposit=guest_deposit,
                status="GUARANTEED",
            )
            .aggregate(
                total=Sum("allocated_amount")
            )["total"]
            or Decimal("0.00")
        )

        available_balance = (
            guest_deposit.balance
            - allocated_elsewhere
        )

        if available_balance < (
            reservation.reservation_deposit.required_amount
        ):
            break

        reservation_deposit = (
            reservation.reservation_deposit
        )

        reservation_deposit.allocated_amount = (
            reservation_deposit.required_amount
        )

        reservation_deposit.status = "GUARANTEED"

        reservation_deposit.save(
            update_fields=[
                "allocated_amount",
                "status",
                "updated_at",
            ]
        )

        guaranteed.append(
            reservation_deposit
        )

    return guaranteed


@transaction.atomic
def create_reservation_guarantee(
    reservation,
    user,
):
    """
    Create or update the reservation guarantee allocation.

    The allocation is an earmark only.

    It does NOT:
        - reduce GuestDeposit.balance
        - create a GuestDepositTransaction
        - create an accounting journal

    A reservation becomes GUARANTEED only when the
    guest has at least 20% of the reservation value
    available in their deposit.
    """

    if not reservation:
        raise ValidationError(
            "Reservation is required."
        )

    if reservation.status != "RESERVED":
        raise ValidationError(
            "Only reserved reservations can be guaranteed."
        )

    if not reservation.guest:
        raise ValidationError(
            "Reservation has no guest."
        )

    if reservation.number_of_rooms < 1:
        raise ValidationError(
            "Reservation must contain at least one room."
        )

    if reservation.check_out_date <= reservation.check_in_date:
        raise ValidationError(
            "Reservation check-out date must be after check-in date."
        )

    rate = getattr(
        reservation.room_category,
        "rate",
        None,
    )

    if not rate or not rate.is_active:
        raise ValidationError(
            "No active rate for this room category."
        )

    nightly_rate = rate.price_per_night

    number_of_nights = (
        reservation.check_out_date
        - reservation.check_in_date
    ).days

    reservation_value = calculate_reservation_value(
        nightly_rate=nightly_rate,
        number_of_rooms=reservation.number_of_rooms,
        number_of_nights=number_of_nights,
    )

    required_amount = calculate_required_guarantee(
        reservation_value=reservation_value,
    )

    cancellation_deadline = calculate_cancellation_deadline(
        reservation.check_in_date,
    )

    hotel = reservation.hotel

    if reservation.guest.hotel_id != hotel.id:
        raise ValidationError(
            "Reservation guest does not belong to this hotel."
        )

    guest_deposit = (
        GuestDeposit.objects
        .select_for_update()
        .get_or_create(
            guest=reservation.guest,
            hotel=hotel,
            defaults={
                "balance": Decimal("0.00"),
            },
        )[0]
    )

    reservation_deposit = (
        ReservationDeposit.objects
        .select_for_update()
        .filter(
            reservation=reservation,
        )
        .first()
    )

    if reservation_deposit:
        if reservation_deposit.status not in (
            "PENDING",
            "GUARANTEED",
        ):
            raise ValidationError(
                "This reservation guarantee cannot be modified."
            )

    active_allocations = (
        ReservationDeposit.objects
        .filter(
            guest_deposit=guest_deposit,
            status="GUARANTEED",
        )
    )

    if reservation_deposit:
        active_allocations = active_allocations.exclude(
            id=reservation_deposit.id
        )

    from django.db.models import Sum

    allocated_elsewhere = (
        active_allocations.aggregate(
            total=Sum("allocated_amount")
        )["total"]
        or Decimal("0.00")
    )

    available_balance = (
        guest_deposit.balance
        - allocated_elsewhere
    )

    if available_balance < 0:
        available_balance = Decimal("0.00")

    if available_balance < required_amount:
        if reservation_deposit:
            reservation_deposit.nightly_rate = nightly_rate
            reservation_deposit.number_of_rooms = (
                reservation.number_of_rooms
            )
            reservation_deposit.number_of_nights = (
                number_of_nights
            )
            reservation_deposit.reservation_value = (
                reservation_value
            )
            reservation_deposit.required_amount = (
                required_amount
            )
            reservation_deposit.allocated_amount = (
                Decimal("0.00")
            )
            reservation_deposit.cancellation_deadline = (
                cancellation_deadline
            )
            reservation_deposit.status = "PENDING"

            reservation_deposit.save(
                update_fields=[
                    "nightly_rate",
                    "number_of_rooms",
                    "number_of_nights",
                    "reservation_value",
                    "required_amount",
                    "allocated_amount",
                    "cancellation_deadline",
                    "status",
                    "updated_at",
                ]
            )

        else:
            reservation_deposit = (
                ReservationDeposit.objects.create(
                    reservation=reservation,
                    guest_deposit=guest_deposit,
                    nightly_rate=nightly_rate,
                    number_of_rooms=reservation.number_of_rooms,
                    number_of_nights=number_of_nights,
                    reservation_value=reservation_value,
                    required_amount=required_amount,
                    allocated_amount=Decimal("0.00"),
                    cancellation_deadline=cancellation_deadline,
                    status="PENDING",
                )
            )

        return reservation_deposit

    if reservation_deposit:
        reservation_deposit.nightly_rate = nightly_rate
        reservation_deposit.number_of_rooms = (
            reservation.number_of_rooms
        )
        reservation_deposit.number_of_nights = (
            number_of_nights
        )
        reservation_deposit.reservation_value = (
            reservation_value
        )
        reservation_deposit.required_amount = (
            required_amount
        )
        reservation_deposit.allocated_amount = (
            required_amount
        )
        reservation_deposit.cancellation_deadline = (
            cancellation_deadline
        )
        reservation_deposit.status = "GUARANTEED"

        reservation_deposit.save(
            update_fields=[
                "nightly_rate",
                "number_of_rooms",
                "number_of_nights",
                "reservation_value",
                "required_amount",
                "allocated_amount",
                "cancellation_deadline",
                "status",
                "updated_at",
            ]
        )

    else:
        reservation_deposit = (
            ReservationDeposit.objects.create(
                reservation=reservation,
                guest_deposit=guest_deposit,
                nightly_rate=nightly_rate,
                number_of_rooms=reservation.number_of_rooms,
                number_of_nights=number_of_nights,
                reservation_value=reservation_value,
                required_amount=required_amount,
                allocated_amount=required_amount,
                cancellation_deadline=cancellation_deadline,
                status="GUARANTEED",
            )
        )

    return reservation_deposit

@transaction.atomic
def cancel_reservation(
    reservation,
    user,
):
    """
    Cancel a RESERVED reservation and release or forfeit its guarantee.

    Rules:
    - Pending/un-guaranteed reservation:
        cancel with no financial effect.
    - Guaranteed reservation cancelled on/before the deadline:
        release the entire earmark.
    - Guaranteed reservation cancelled after the deadline:
        forfeit 20% of the reservation value and release any excess
        earmark back to the guest's available deposit.

    The retained 20% is transferred from Guest Deposits liability
    to Other Revenue.

    After cancellation, newly available deposit funds are offered
    to other pending reservations for the same guest, oldest first.
    """

    if not reservation:
        raise ValidationError("Reservation is required.")

    # Lock the reservation so two cancellation requests cannot
    # process the same reservation simultaneously.
    reservation = (
        Reservation.objects
        .select_for_update()
        .select_related("guest", "hotel")
        .get(pk=reservation.pk)
    )

    if reservation.status != "RESERVED":
        raise ValidationError(
            "Only RESERVED reservations can be cancelled."
        )

    reservation_deposit = (
        ReservationDeposit.objects
        .select_for_update()
        .select_related("guest_deposit")
        .filter(reservation=reservation)
        .first()
    )

    # No guarantee record means there is nothing to release or forfeit.
    if not reservation_deposit:
        reservation.status = "CANCELLED"
        reservation.save(update_fields=["status"])
        return {
            "reservation": reservation,
            "reservation_deposit": None,
            "cancelled": True,
            "forfeited_amount": Decimal("0.00"),
            "released_amount": Decimal("0.00"),
        }

    guest_deposit = (
        GuestDeposit.objects
        .select_for_update()
        .get(pk=reservation_deposit.guest_deposit_id)
    )

    allocated_amount = (
        reservation_deposit.allocated_amount
        or Decimal("0.00")
    )

    forfeited_amount = Decimal("0.00")
    released_amount = Decimal("0.00")

    if reservation_deposit.status == "GUARANTEED":
        now = timezone.localtime()

        if (
            reservation_deposit.cancellation_deadline
            and now > reservation_deposit.cancellation_deadline
        ):
            # Hotel retains exactly 20% of the reservation value.
            forfeited_amount = calculate_required_guarantee(
                reservation_deposit.reservation_value
            )

            # Never forfeit more than the amount actually earmarked.
            if forfeited_amount > allocated_amount:
                forfeited_amount = allocated_amount

            # Any earmarked amount above the forfeiture becomes available.
            released_amount = allocated_amount - forfeited_amount

            if forfeited_amount > guest_deposit.balance:
                raise ValidationError(
                    "Guest deposit balance is insufficient to process "
                    "the reservation guarantee forfeiture."
                )

            if forfeited_amount > 0:
                guest_deposit.balance -= forfeited_amount
                guest_deposit.save(
                    update_fields=["balance", "updated_at"]
                )

                GuestDepositTransaction.objects.create(
                    deposit=guest_deposit,
                    transaction_type="FORFEITED",
                    amount=forfeited_amount,
                    reference=f"RESERVATION-{reservation.id}-FORFEIT",
                    note=(
                        f"20% reservation guarantee forfeited after "
                        f"late cancellation of Reservation "
                        f"#{reservation.id}."
                    ),
                    created_by=user,
                )

                record_transaction(
                    debit_system_key="guest_deposits",
                    credit_system_key="other_revenue",
                    amount=forfeited_amount,
                    description=(
                        f"Reservation #{reservation.id} late "
                        f"cancellation guarantee forfeiture"
                    ),
                    hotel=reservation.hotel,
                    created_by=user,
                    entry_type="NORMAL",
                    reference=(
                        f"RESERVATION-{reservation.id}-FORFEIT"
                    ),
                )

            reservation_deposit.status = "FORFEITED"

        else:
            # Cancellation within the allowed window:
            # the entire earmark is released.
            released_amount = allocated_amount
            reservation_deposit.status = "RELEASED"

    elif reservation_deposit.status == "PENDING":
        # No funds are earmarked.
        reservation_deposit.status = "RELEASED"

    elif reservation_deposit.status in (
        "RELEASED",
        "FORFEITED",
        "REFUNDED",
    ):
        raise ValidationError(
            "This reservation guarantee has already been processed."
        )

    else:
        raise ValidationError(
            "Invalid reservation guarantee status."
        )

    # allocated_amount represents the current earmark.
    # Once cancelled, there is no remaining earmark.
    reservation_deposit.allocated_amount = Decimal("0.00")
    reservation_deposit.save(
        update_fields=[
            "allocated_amount",
            "status",
            "updated_at",
        ]
    )

    reservation.status = "CANCELLED"
    reservation.save(update_fields=["status"])

    # Newly available deposit funds can now guarantee another
    # pending reservation for this guest.
    newly_guaranteed = allocate_pending_reservation_guarantees(
        guest=reservation.guest,
        hotel=reservation.hotel,
        user=user,
    )

    return {
        "reservation": reservation,
        "reservation_deposit": reservation_deposit,
        "cancelled": True,
        "forfeited_amount": forfeited_amount,
        "released_amount": released_amount,
        "newly_guaranteed": newly_guaranteed,
    }