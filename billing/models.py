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
        Apply one room-night charge using the group's negotiated rate when
        this folio belongs to a corporate allocation. Individual stays retain
        the standard room-category rate.
        """
        if self.folio_type != "ROOM" or self.is_closed:
            return

        if not self.room or not self.room.category:
            raise ValidationError("Room or category missing.")

        today = timezone.localdate()

        if self.last_room_charge_date is None:
            charge_date = timezone.localdate(self.check_in_at)
        else:
            checkout_boundary = timezone.make_aware(
                timezone.datetime.combine(
                    self.last_room_charge_date,
                    timezone.datetime.min.time(),
                )
            ).replace(hour=12, minute=0, second=0, microsecond=0)

            if timezone.now() <= checkout_boundary:
                return

            if self.check_out_at:
                scheduled_checkout = self.check_out_at
                if timezone.is_naive(scheduled_checkout):
                    scheduled_checkout = timezone.make_aware(scheduled_checkout)
                if timezone.now() >= scheduled_checkout:
                    return

            charge_date = today
            if self.last_room_charge_date == charge_date:
                return

        from billing.services.room_pricing import create_room_night_charge
        create_room_night_charge(
            folio=self,
            charge_date=charge_date,
            charged_by=charged_by,
        )

        self.last_room_charge_date = charge_date
        self.save(update_fields=["last_room_charge_date"])

    @property
    def total_charges(self):
        return sum(c.amount for c in self.charges.all())

    @property
    def total_payments(self):
        # Allocation-aware receipts reduce the folio only by the amount
        # applied to its verified balance. Charge-level allocations are kept
        # separately where available; legacy balances are not guessed by charge.
        # Any unapplied receipt remainder is held as a guest liability.
        legacy_total = (
            self.payments.filter(allocation_tracking_enabled=False)
            .aggregate(total=models.Sum("amount"))["total"]
            or Decimal("0.00")
        )
        tracked_total = (
            self.payments.filter(allocation_tracking_enabled=True)
            .aggregate(total=models.Sum("applied_amount"))["total"]
            or Decimal("0.00")
        )
        return legacy_total + tracked_total

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
    allocation_enabled = models.BooleanField(
        default=True,
        help_text="Only charges created after allocation rollout or explicitly reconciled are eligible for automatic allocation.",
    )

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
    allocation_tracking_enabled = models.BooleanField(
        default=False,
        help_text="Folio balance uses applied allocations rather than the full receipt amount.",
    )
    applied_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount of this receipt applied to the folio; excess is held as guest credit.",
    )
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

class ChargePaymentAllocation(models.Model):
    """Auditable application of one folio payment to one specific charge."""

    ALLOCATION_TYPE_CHOICES = (
        ("FIFO", "Folio FIFO"),
        ("POS_DIRECT", "POS order direct"),
    )

    charge = models.ForeignKey(
        Charge,
        on_delete=models.PROTECT,
        related_name="payment_allocations",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="charge_allocations",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    allocation_type = models.CharField(
        max_length=20,
        choices=ALLOCATION_TYPE_CHOICES,
        default="FIFO",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charge_payment_allocations_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="charge_payment_allocation_amount_positive",
            ),
        ]

    def clean(self):
        if self.charge_id and self.payment_id:
            if self.charge.folio_id != self.payment.folio_id:
                raise ValidationError(
                    "Payment and charge must belong to the same folio."
                )
            if self.charge.folio.hotel_id != self.payment.folio.hotel_id:
                raise ValidationError(
                    "Payment and charge must belong to the same hotel."
                )
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Allocation amount must be greater than zero.")

    def __str__(self):
        return f"Payment #{self.payment_id} → Charge #{self.charge_id}: {self.amount}"



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
        ("UNAPPLIED_CREDIT", "Unapplied Folio Credit"),
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

    EXECUTION_METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("POS", "POS terminal"),
        ("TRANSFER", "Bank transfer"),
        ("ROOM_REVERSAL", "Reverse unpaid room charge"),
    )
    execution_method = models.CharField(
        max_length=20, choices=EXECUTION_METHOD_CHOICES, blank=True
    )
    execution_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True,
        on_delete=models.PROTECT, related_name="refund_executions"
    )
    execution_reference = models.CharField(max_length=100, blank=True)
    execution_note = models.TextField(blank=True)
    execution_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    external_refund_confirmed = models.BooleanField(default=False)

    def __str__(self):
        if self.source_type == "POS_ORDER" and self.pos_order_id:
            source = f"POS Order #{self.pos_order_id}"
        elif self.source_type == "CHARGE" and self.charge_id:
            source = f"Charge #{self.charge_id}"
        else:
            source = "Unknown Source"

        return f"Refund Request #{self.id} - {source} ({self.status})"

class RefundExecution(models.Model):
    """Auditable record of an actual Restaurant POS bank payout."""
    refund_request = models.OneToOneField(RefundRequest, on_delete=models.PROTECT, related_name="payout")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    forfeited_amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="restaurant_refund_payouts")
    reference = models.CharField(max_length=100)
    note = models.TextField(blank=True)
    executed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="restaurant_refund_payouts")
    executed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"POS refund payout #{self.pk} — ₦{self.amount:,.2f}"


# ============================================================================
# COMPANY AND GROUP RESERVATIONS
# These models extend the existing individual Guest / Reservation / Folio
# records. Individual folios remain the financial source of truth.
# ============================================================================

class Company(models.Model):
    """A corporate customer belonging to one hotel."""

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="corporate_customers",
    )
    name = models.CharField(max_length=200)
    registration_number = models.CharField(max_length=100, blank=True)
    billing_address = models.TextField(blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    tax_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="companies_created",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_company_name_per_hotel",
            ),
        ]

    def __str__(self):
        return self.name


class GroupReservation(models.Model):
    """Master corporate booking; room stays remain individual reservations."""

    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("CONFIRMED", "Confirmed"),
        ("IN_HOUSE", "In House"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    )

    hotel = models.ForeignKey(
        Hotel, on_delete=models.PROTECT, related_name="group_reservations"
    )
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="group_reservations"
    )
    reference = models.CharField(max_length=40)
    name = models.CharField(
        max_length=200, blank=True,
        help_text="Optional event, project or group name.",
    )
    check_in_date = models.DateField()
    check_out_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="DRAFT"
    )
    agreement_notes = models.TextField(blank=True)
    ROOM_BILLING_PARTY_CHOICES = (
        ("COMPANY", "Company"),
        ("INDIVIDUAL", "Occupant"),
    )
    room_billing_party = models.CharField(
        max_length=12,
        choices=ROOM_BILLING_PARTY_CHOICES,
        default="COMPANY",
        help_text="Who is responsible for negotiated room-night charges.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="group_reservations_created",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "reference"],
                name="unique_group_reference_per_hotel",
            ),
        ]

    def clean(self):
        if self.hotel_id and self.company_id and self.company.hotel_id != self.hotel_id:
            raise ValidationError("Company and group reservation must belong to the same hotel.")
        if self.check_in_date and self.check_out_date and self.check_out_date <= self.check_in_date:
            raise ValidationError("Group check-out date must be after check-in date.")

    def __str__(self):
        return f"{self.reference} – {self.company.name}"


class GroupReservationRate(models.Model):
    """Negotiated nightly rate for one room category within a group agreement."""

    group_reservation = models.ForeignKey(
        GroupReservation,
        on_delete=models.PROTECT,
        related_name="room_rates",
    )
    room_category = models.ForeignKey(
        RoomCategory,
        on_delete=models.PROTECT,
        related_name="group_reservation_rates",
    )
    nightly_rate = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["room_category__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["group_reservation", "room_category"],
                name="unique_group_room_category_rate",
            ),
            models.CheckConstraint(
                condition=models.Q(nightly_rate__gt=0),
                name="group_room_rate_positive",
            ),
        ]

    def clean(self):
        if self.nightly_rate is not None and self.nightly_rate <= 0:
            raise ValidationError("Negotiated nightly rate must be greater than zero.")

    def __str__(self):
        return f"{self.group_reservation.reference} – {self.room_category.name}: {self.nightly_rate}"


class GroupReservationRoom(models.Model):
    """One named occupant and one room allocation within a group booking."""

    STATUS_CHOICES = (
        ("RESERVED", "Reserved"),
        ("CHECKED_IN", "Checked In"),
        ("CHECKED_OUT", "Checked Out"),
        ("CANCELLED", "Cancelled"),
    )

    group_reservation = models.ForeignKey(
        GroupReservation,
        on_delete=models.PROTECT,
        related_name="room_allocations",
    )
    guest = models.ForeignKey(
        Guest, on_delete=models.PROTECT, related_name="group_room_allocations"
    )
    room_category = models.ForeignKey(
        RoomCategory, on_delete=models.PROTECT,
        related_name="group_room_allocations",
    )
    room = models.ForeignKey(
        Room, on_delete=models.PROTECT, null=True, blank=True,
        related_name="group_room_allocations",
    )
    reservation = models.OneToOneField(
        Reservation, on_delete=models.PROTECT, null=True, blank=True,
        related_name="group_allocation",
    )
    folio = models.OneToOneField(
        Folio, on_delete=models.PROTECT, null=True, blank=True,
        related_name="group_allocation",
    )
    check_in_date = models.DateField()
    check_out_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="RESERVED"
    )
    occupant_note = models.TextField(blank=True)
    negotiated_nightly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional occupant-specific nightly rate override.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        group = self.group_reservation
        if self.guest_id and self.guest.hotel_id != group.hotel_id:
            raise ValidationError("Occupant must belong to the group's hotel.")
        # RoomCategory is a shared catalogue model and is not hotel-scoped.
        # Hotel ownership is established by the selected Room instead.
        if self.room_id and self.room.hotel_id != group.hotel_id:
            raise ValidationError("Room must belong to the group's hotel.")
        if self.room_id and self.room_category_id and self.room.category_id != self.room_category_id:
            raise ValidationError("Assigned room does not match the selected category.")
        if self.reservation_id:
            if self.reservation.hotel_id != group.hotel_id or self.reservation.guest_id != self.guest_id:
                raise ValidationError("Linked reservation must belong to this occupant and hotel.")
        if self.folio_id:
            if self.folio.hotel_id != group.hotel_id or self.folio.guest_id != self.guest_id:
                raise ValidationError("Linked folio must belong to this occupant and hotel.")
        if self.check_in_date and self.check_out_date and self.check_out_date <= self.check_in_date:
            raise ValidationError("Room allocation check-out must be after check-in.")
        if self.negotiated_nightly_rate is not None and self.negotiated_nightly_rate <= 0:
            raise ValidationError("Occupant-specific nightly rate must be greater than zero.")

    def __str__(self):
        return f"{self.group_reservation.reference} – {self.guest.full_name}"


class CompanyServicePolicy(models.Model):
    """Services covered by a company's agreement for a particular group stay.

    A policy is scoped to a group reservation so later contract changes do not
    silently rewrite the terms of an earlier stay.
    """

    COVERAGE_CHOICES = (
        ("COVERED", "Company Covered"),
        ("EXCLUDED", "Occupant Pays"),
    )

    group_reservation = models.ForeignKey(
        GroupReservation, on_delete=models.PROTECT,
        related_name="service_policies",
    )
    service_code = models.CharField(
        max_length=60,
        help_text="Stable service key, e.g. ROOM, BREAKFAST, LAUNDRY.",
    )
    service_name = models.CharField(max_length=120)
    coverage = models.CharField(
        max_length=12, choices=COVERAGE_CHOICES, default="EXCLUDED"
    )
    amount_limit_per_occupant = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Optional currency allowance per occupant for the entire stay.",
    )
    quantity_limit_per_occupant = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Optional service quantity allowance per occupant for the entire stay.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["service_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["group_reservation", "service_code"],
                name="unique_group_service_policy",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(amount_limit_per_occupant__isnull=True)
                    | models.Q(amount_limit_per_occupant__gte=0)
                ),
                name="company_service_amount_limit_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(quantity_limit_per_occupant__isnull=True)
                    | models.Q(quantity_limit_per_occupant__gte=0)
                ),
                name="company_service_quantity_limit_nonnegative",
            ),
        ]

    def clean(self):
        if self.amount_limit_per_occupant is not None and self.amount_limit_per_occupant < 0:
            raise ValidationError("Amount allowance cannot be negative.")
        if self.quantity_limit_per_occupant is not None and self.quantity_limit_per_occupant < 0:
            raise ValidationError("Quantity allowance cannot be negative.")

    def __str__(self):
        return f"{self.group_reservation.reference}: {self.service_name}"


# Charges retain the payer decision made when the service is posted. The
# default is INDIVIDUAL so all existing charge creation paths remain unchanged.
Charge.BILLING_PARTY_CHOICES = (
    ("INDIVIDUAL", "Occupant"),
    ("COMPANY", "Company"),
)
Charge.add_to_class(
    "billing_party",
    models.CharField(
        max_length=12,
        choices=Charge.BILLING_PARTY_CHOICES,
        default="INDIVIDUAL",
        db_index=True,
    ),
)
Charge.add_to_class(
    "group_room_allocation",
    models.ForeignKey(
        GroupReservationRoom,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charges",
    ),
)
