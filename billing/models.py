from django.db import models
from django.conf import settings
from inventory.models import Department
# from rooms.models import Room
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.utils import timezone
from inventory.models import Hotel
from rooms.models import Room,RoomCategory



User = settings.AUTH_USER_MODEL


class Guest(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="guests"
    )

    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)

    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)

    nationality = models.CharField(max_length=100, blank=True)

    id_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Passport / National ID"
    )

    id_document = models.ImageField(
        upload_to="guest_ids/%Y/%m/",
        blank=True,
        null=True,
        help_text="Guest passport, national ID, driver's licence or other identity document.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.full_name


# =========================
# FOLIO (FINANCIAL CONTAINER)
# =========================
from billing.models import Guest

class Folio(models.Model):
    FOLIO_TYPE = (
        ("ROOM", "Room Folio"),
        ("WALKIN", "Walk-in Folio"),
    )

    folio_type = models.CharField(max_length=10, choices=FOLIO_TYPE)

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="folios"
    )

    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    last_room_charge_date = models.DateField(
        null=True,
        blank=True,
        help_text="Last date room charge was applied"
    )

    guest = models.ForeignKey(
        Guest,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )
    check_in_at = models.DateTimeField(default=timezone.now)
    check_out_at = models.DateTimeField(null=True, blank=True)

    is_closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["room"],
                condition=models.Q(
                    folio_type="ROOM",
                    is_closed=False
                ),
                name="unique_active_room_folio"
            )
        ]

    def charge_room_stay(self, charged_by):
        pass  # deprecated, using daily charges

    def clean(self):
        
        # 🔐 HARD VALIDATION
        if self.folio_type == "ROOM":
            if not self.room:
                raise ValidationError("Room folio must have a room.")
            if not self.guest:
                raise ValidationError("Room folio must have a guest.")

        if self.folio_type == "WALKIN" and self.room:
            raise ValidationError("Walk-in folio cannot be linked to a room.")

    def __str__(self):
        if self.folio_type == "ROOM":
            return f"Room {self.room.room_number} – {self.guest}"
        return f"Walk-in – {self.guest or 'Guest'}"


    def apply_daily_room_charge(self, charged_by=None):
        """
        Apply one room-night charge.

        CCH billing rule:
        - First room night is charged immediately at check-in.
        - The same room night is not charged again merely because
        the calendar date changes.
        - A subsequent room night becomes chargeable only after
        the normal 12:00 PM checkout boundary has passed.
        """

        if self.folio_type != "ROOM" or self.is_closed:
            return

        if not self.room or not self.room.category:
            raise ValidationError("Room or category missing.")

        rate = getattr(self.room.category, "rate", None)

        if not rate or not rate.is_active:
            raise ValidationError(
                "No active rate for this room category."
            )

        today = timezone.localdate()

        # First room-night charge:
        # check-in charges the current room night immediately.
        if self.last_room_charge_date is None:
            charge_date = timezone.localdate(self.check_in_at)

        else:
            # The guest's next room night is not billable until
            # the normal 12:00 PM checkout boundary has passed.
            checkout_boundary = timezone.make_aware(
                timezone.datetime.combine(
                    self.last_room_charge_date,
                    timezone.datetime.min.time(),
                )
            ).replace(hour=12, minute=0, second=0, microsecond=0)

            if timezone.now() <= checkout_boundary:
                return

            # Do not create another room-night charge if the guest's
            # current stay has already reached its scheduled checkout.
            if self.check_out_at:
                scheduled_checkout = self.check_out_at

                if timezone.is_naive(scheduled_checkout):
                    scheduled_checkout = timezone.make_aware(
                        scheduled_checkout
                    )

                if timezone.now() >= scheduled_checkout:
                    return

            charge_date = today

            # Prevent duplicate charging of the same room-night.
            if self.last_room_charge_date == charge_date:
                return

        charge = Charge.objects.create(
            folio=self,
            description=f"Room charge – {charge_date}",
            department=Department.objects.get(
                hotel=self.room.hotel,
                department_type="FRONTDESK",
                is_active=True,
            ),
            amount=Decimal(rate.price_per_night),
            reference=f"ROOM-{self.room.room_number}-{charge_date}",
        )

        # Accounting must succeed before the room charge is considered posted.
        from accounting.services.postings.room_charge import (
            post_room_charge,
        )

        post_room_charge(
            self,
            charge.amount,
            charged_by,
        )

        self.last_room_charge_date = charge_date

        self.save(
            update_fields=[
                "last_room_charge_date",
            ]
        )

    @property
    def total_charges(self):
        return sum(c.amount for c in self.charges.all())

    @property
    def total_payments(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def balance(self):
        return self.total_charges - self.total_payments

    from datetime import date

    @property
    def nights(self):
        if not self.check_in_at:
            return 0

        end = self.check_out_at or timezone.now()
        return max((end.date() - self.check_in_at.date()).days, 1)


    @classmethod
    def get_active_room_folio(cls, room):
        if not room or not room.hotel:
            return None

        return cls.objects.filter(
            folio_type="ROOM",
            room=room,
            hotel=room.hotel,   # 🔒 Hotel safety
            is_closed=False
        ).select_related("guest").first()

# =========================
# CHARGE (CONSUMPTION)
# =========================
class Charge(models.Model):
    folio = models.ForeignKey(
        Folio,
        related_name='charges',
        on_delete=models.PROTECT
    )
    description = models.CharField(max_length=255)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.description} - {self.amount}"

class FolioRoomHistory(models.Model):

    REASON_CHOICES = (
        ("CHECK_IN", "Check-in"),
        ("ROOM_CHANGE", "Room Change"),
    )

    folio = models.ForeignKey(
        Folio,
        related_name="room_history",
        on_delete=models.PROTECT,
    )

    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.PROTECT,
        related_name="folio_room_history",
    )

    assigned_at = models.DateTimeField(
        auto_now_add=True,
    )

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["assigned_at"]

    def __str__(self):
        return (
            f"Folio #{self.folio_id} — "
            f"Room {self.room.room_number}"
        )


# =========================
# PAYMENT (SETTLEMENT)
# =========================
class Payment(models.Model):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("POS", "POS"),
        ("TRANSFER", "Bank Transfer"),
        ("DEPOSIT", "Guest Deposit"),
    )

    folio = models.ForeignKey(
        Folio,
        related_name="payments",
        on_delete=models.PROTECT
    )
    account = models.ForeignKey(
        "accounting.Account",
        related_name="payments",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    shift = models.ForeignKey(
        "restaurant.Shift",
        related_name="payments",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )    
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="POS slip number / Transfer reference"
    )

    note = models.TextField(blank=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    collected_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.method} – {self.amount}"


# =========================
# GUEST DEPOSIT
# =========================

class GuestDeposit(models.Model):
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="guest_deposits",
    )

    guest = models.ForeignKey(
        Guest,
        on_delete=models.PROTECT,
        related_name="deposits",
    )

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "guest"],
                name="unique_guest_deposit_per_hotel",
            )
        ]

    def __str__(self):
        return (
            f"{self.guest.full_name} – "
            f"Deposit ₦{self.balance:,.2f}"
        )


class GuestDepositTransaction(models.Model):

    TYPE_CHOICES = (
        ("RECEIVED", "Deposit Received"),
        ("APPLIED", "Deposit Applied"),
        ("REFUNDED", "Deposit Refunded"),
        ("FORFEITED", "Reservation Guarantee Forfeited"),

    )

    deposit = models.ForeignKey(
        GuestDeposit,
        on_delete=models.PROTECT,
        related_name="transactions",
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    folio = models.ForeignKey(
        Folio,
        on_delete=models.PROTECT,
        related_name="deposit_transactions",
        null=True,
        blank=True,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    note = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return (
            f"{self.transaction_type} – "
            f"₦{self.amount:,.2f}"
        )
    

class Reservation(models.Model):

    STATUS_CHOICES = (
        ("RESERVED", "Reserved"),
        ("CHECKED_IN", "Checked In"),
        ("CANCELLED", "Cancelled"),
        ("NO_SHOW", "No Show"),
    )

    guest = models.ForeignKey(
        Guest,
        on_delete=models.PROTECT,
        related_name="reservations"
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT
    )

    room_category = models.ForeignKey(
        RoomCategory,
        on_delete=models.PROTECT
    )

    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    number_of_rooms = models.PositiveIntegerField(
        default=1,
        help_text="Number of rooms reserved under this reservation.",
    )

    check_in_date = models.DateField()
    check_out_date = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="RESERVED"
    )

    source = models.CharField(
        max_length=50,
        blank=True,
        help_text="Walk-in, Phone, Website, Booking.com etc"
    )

    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    def __str__(self):
        return f"Reservation {self.id} - {self.guest}"

class ReservationDeposit(models.Model):

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("GUARANTEED", "Guaranteed"),
        ("RELEASED", "Released"),
        ("FORFEITED", "Forfeited"),
        ("REFUNDED", "Refunded"),
    )

    reservation = models.OneToOneField(
        Reservation,
        on_delete=models.PROTECT,
        related_name="reservation_deposit",
    )

    guest_deposit = models.ForeignKey(
        GuestDeposit,
        on_delete=models.PROTECT,
        related_name="reservation_allocations",
    )

    nightly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    number_of_rooms = models.PositiveIntegerField(
        default=1,
    )

    number_of_nights = models.PositiveIntegerField(
        default=1,
    )

    reservation_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    required_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    allocated_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    cancellation_deadline = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"Reservation {self.reservation_id} "
            f"– ₦{self.allocated_amount:,.2f}"
        )
    @property
    def is_guaranteed(self):
        return self.status == "GUARANTEED"

# =========================
# REFUND REQUEST
# =========================

class RefundRequest(models.Model):

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("KITCHEN_CONFIRMATION", "Awaiting Kitchen Confirmation"),
        ("REJECTED", "Rejected"),
        ("COMPLETED", "Completed"),
    )

    SOURCE_CHOICES = (
        ("POS_ORDER", "Restaurant Order"),
        ("CHARGE", "Charge"),
    )

    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
    )

    pos_order = models.ForeignKey(
        "restaurant.POSOrder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="refund_requests",
    )

    charge = models.ForeignKey(
        "billing.Charge",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="refund_requests",
    )

    reason = models.TextField()

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="refund_requests_created",
    )

    requested_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refund_requests_approved",
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    rejection_reason = models.TextField(
        blank=True,
    )

    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refund_requests_executed",
    )

    executed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    KITCHEN_OUTCOME_CHOICES = (
        ("UNPREPARED_RESTOCK", "Unprepared — return to stock"),
        ("PREPARED_WASTE", "Prepared — record as waste"),
    )

    kitchen_outcome = models.CharField(max_length=24, choices=KITCHEN_OUTCOME_CHOICES, blank=True)
    kitchen_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="kitchen_confirmed_refunds")
    kitchen_confirmed_at = models.DateTimeField(null=True, blank=True)
    kitchen_confirmation_note = models.TextField(blank=True)
    director_authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="refunds_authorized")
    director_authorized_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        if self.source_type == "POS_ORDER" and self.pos_order_id:
            source = f"POS Order #{self.pos_order_id}"
        elif self.source_type == "CHARGE" and self.charge_id:
            source = f"Charge #{self.charge_id}"
        else:
            source = "Unknown Source"

        return f"Refund Request #{self.id} - {source} ({self.status})"