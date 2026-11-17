from django.db import models
from django.conf import settings
from inventory.models import Department
# from rooms.models import Room
from django.core.exceptions import ValidationError
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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

    reservation = models.ForeignKey(
        "billing.Reservation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="folios",
    )
    room_discount_percent = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("0.00"),
        help_text="Approved fixed receptionist discount copied from the reservation or walk-in check-in.",
    )
    room_discount_reason = models.CharField(max_length=255, blank=True)
    room_discount_applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="room_discounts_applied",
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

        from billing.models import ComplimentaryRoomNight
        waiver = ComplimentaryRoomNight.objects.select_for_update().filter(
            folio=self,
            reservation=self.reservation,
            room_night_date=charge_date,
            status="APPROVED",
        ).first()
        if waiver is not None:
            waiver.status = "CONSUMED"
            waiver.consumed_at = timezone.now()
            waiver.save(update_fields=["status", "consumed_at"])
            self.last_room_charge_date = charge_date
            self.save(update_fields=["last_room_charge_date"])
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
        """Charges payable by the occupant; company-billed charges stay on the group ledger."""
        return sum(c.amount for c in self.charges.filter(billing_party="INDIVIDUAL"))

    @property
    def total_company_charges(self):
        """Company-responsible charges recorded on this occupant's operational folio."""
        return sum(c.amount for c in self.charges.filter(billing_party="COMPANY"))

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
    original_room_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_percentage = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.00"))

    BILLING_PARTY_CHOICES = (
        ("INDIVIDUAL", "Occupant"),
        ("COMPANY", "Company"),
    )
    billing_party = models.CharField(
        max_length=12,
        choices=BILLING_PARTY_CHOICES,
        default="INDIVIDUAL",
        db_index=True,
    )
    group_room_allocation = models.ForeignKey(
        "billing.GroupReservationRoom",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charges",
    )
    room_night_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="The room-night date represented by this room charge.",
    )
    room_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Immutable nightly rate snapshot used when this charge was posted.",
    )
    ROOM_RATE_SOURCE_CHOICES = (
        ("STANDARD", "Standard category rate"),
        ("GROUP", "Group negotiated rate"),
        ("OVERRIDE", "Occupant override"),
    )
    room_rate_source = models.CharField(
        max_length=12,
        choices=ROOM_RATE_SOURCE_CHOICES,
        blank=True,
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
    room_discount_percent = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("0.00"),
        help_text="Only 0%, 5%, 10% or 15% may be selected.",
    )
    room_discount_reason = models.CharField(max_length=255, blank=True)
    room_discount_applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="reservation_room_discounts_applied",
    )

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



class ComplimentaryRoomNight(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending approval"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("CONSUMED", "Consumed"),
    )

    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.PROTECT,
        related_name="complimentary_room_nights",
    )
    folio = models.ForeignKey(
        Folio,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="complimentary_room_nights",
    )
    room_night_date = models.DateField()
    waived_amount = models.DecimalField(max_digits=12, decimal_places=2)
    rate_source = models.CharField(max_length=12, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="PENDING")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="complimentary_nights_requested",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="complimentary_nights_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["room_night_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "room_night_date"],
                name="unique_complimentary_reservation_night",
            ),
        ]

    def clean(self):
        if self.reservation_id:
            if self.room_night_date < self.reservation.check_in_date or self.room_night_date >= self.reservation.check_out_date:
                raise ValidationError("Complimentary date must fall within the reservation stay.")
            if self.folio_id and self.folio.reservation_id != self.reservation_id:
                raise ValidationError("Complimentary night folio must belong to the same reservation.")
        if self.waived_amount is not None and self.waived_amount < 0:
            raise ValidationError("Waived amount cannot be negative.")

    def __str__(self):
        return f"Complimentary night {self.reservation_id} – {self.room_night_date} ({self.status})"



# ============================================================================
# CORPORATE DEPOSITS
# Corporate funds are liabilities until applied to company receivables or
# refunded. They are deliberately separate from individual GuestDeposit.
# ============================================================================

class CompanyDeposit(models.Model):
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="company_deposits",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="deposits",
    )
    balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "company"],
                name="unique_company_deposit_per_hotel",
            ),
        ]

    def clean(self):
        if self.hotel_id and self.company_id and self.company.hotel_id != self.hotel_id:
            raise ValidationError("Company deposit and company must belong to the same hotel.")
        if self.balance is not None and self.balance < 0:
            raise ValidationError("Company deposit balance cannot be negative.")

    def __str__(self):
        return f"{self.company.name} – Deposit ₦{self.balance:,.2f}"


class CompanyDepositTransaction(models.Model):
    TYPE_CHOICES = (
        ("RECEIVED", "Deposit Received"),
        ("APPLIED", "Applied to Company Invoice"),
        ("REFUNDED", "Deposit Refunded"),
        ("ADJUSTMENT", "Balance Adjustment"),
    )
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("POS", "POS"),
        ("TRANSFER", "Bank Transfer"),
    )

    deposit = models.ForeignKey(
        CompanyDeposit,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    transaction_type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_method = models.CharField(
        max_length=12,
        choices=METHOD_CHOICES,
        blank=True,
    )
    bank_account = models.ForeignKey(
        "accounting.BankAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="company_deposit_transactions",
    )
    company_invoice = models.ForeignKey(
        "billing.CompanyInvoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deposit_applications",
    )
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_deposit_transactions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="company_deposit_transaction_amount_positive",
            ),
        ]

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Company deposit transaction amount must be greater than zero.")
        if self.bank_account_id and self.deposit_id:
            if self.bank_account.hotel_id != self.deposit.hotel_id or self.bank_account.account.hotel_id != self.deposit.hotel_id:
                raise ValidationError("Bank account must belong to the company deposit hotel.")
        if self.transaction_type == "APPLIED":
            if not self.company_invoice_id:
                raise ValidationError("An applied company deposit must reference its company invoice.")
            if self.deposit_id and self.company_invoice_id:
                invoice = self.company_invoice
                if invoice.hotel_id != self.deposit.hotel_id or invoice.company_id != self.deposit.company_id:
                    raise ValidationError("Deposit and invoice must belong to the same company and hotel.")
        elif self.company_invoice_id:
            raise ValidationError("Only deposit applications may reference a company invoice.")

    def __str__(self):
        return f"{self.transaction_type} – ₦{self.amount:,.2f}"


# ============================================================================
# CORPORATE QUOTATIONS AND PROFORMA INVOICES
# These are commercial documents only. They do not create accounting entries.
# ============================================================================

class CompanyDocument(models.Model):
    TYPE_CHOICES = (
        ("QUOTATION", "Quotation"),
        ("PROFORMA", "Proforma Invoice"),
    )
    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("ISSUED", "Issued"),
        ("VOID", "Void"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="company_documents")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="documents")
    group_reservation = models.ForeignKey(
        GroupReservation, on_delete=models.PROTECT, related_name="company_documents"
    )
    document_type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    document_number = models.CharField(max_length=40)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="DRAFT")
    issue_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)

    # Recipient and booking snapshots preserve the issued document's contents.
    company_name_snapshot = models.CharField(max_length=200)
    company_address_snapshot = models.TextField(blank=True)
    company_contact_snapshot = models.CharField(max_length=150, blank=True)
    company_phone_snapshot = models.CharField(max_length=50, blank=True)
    company_email_snapshot = models.EmailField(blank=True)
    group_reference_snapshot = models.CharField(max_length=40)
    group_name_snapshot = models.CharField(max_length=200, blank=True)
    stay_check_in = models.DateField()
    stay_check_out = models.DateField()

    terms = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    converted_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="converted_documents",
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="company_documents_created",
    )
    issued_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="company_documents_issued",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "document_number"],
                name="unique_company_document_number_per_hotel",
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0) & models.Q(tax_amount__gte=0) & models.Q(total_amount__gte=0),
                name="company_document_totals_nonnegative",
            ),
        ]

    def clean(self):
        if self.company_id and self.hotel_id and self.company.hotel_id != self.hotel_id:
            raise ValidationError("Company document and company must belong to the same hotel.")
        if self.group_reservation_id:
            if self.group_reservation.hotel_id != self.hotel_id:
                raise ValidationError("Group reservation must belong to the document hotel.")
            if self.group_reservation.company_id != self.company_id:
                raise ValidationError("Group reservation must belong to the document company.")
        if self.valid_until and self.valid_until < self.issue_date:
            raise ValidationError("Document validity date cannot be before its issue date.")
        if self.converted_from_id:
            source = self.converted_from
            if source.hotel_id != self.hotel_id or source.company_id != self.company_id:
                raise ValidationError("Converted document must belong to the same hotel and company.")
            if source.document_type != "QUOTATION" or self.document_type != "PROFORMA":
                raise ValidationError("Only a quotation can be converted to a proforma invoice.")

    def __str__(self):
        return f"{self.get_document_type_display()} {self.document_number}"


class CompanyDocumentLine(models.Model):
    document = models.ForeignKey(
        CompanyDocument, on_delete=models.PROTECT, related_name="lines"
    )
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=30, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0.00"))
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="company_doc_line_quantity_positive"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="company_doc_line_price_nonnegative"),
        ]

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Document line quantity must be greater than zero.")
        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError("Document line unit price cannot be negative.")

    def save(self, *args, **kwargs):
        # Numeric Unit values act as multipliers (e.g. 10 rooms x 4 nights).
        # Descriptive legacy values such as "room nights" remain multiplier 1.
        unit_value = (self.unit or "").strip()
        try:
            unit_multiplier = Decimal(unit_value) if unit_value else Decimal("1")
        except (InvalidOperation, TypeError, ValueError):
            unit_multiplier = Decimal("1")
        if unit_multiplier <= 0:
            raise ValidationError("Document line unit multiplier must be greater than zero.")

        self.line_total = (
            (self.quantity or Decimal("0.00"))
            * unit_multiplier
            * (self.unit_price or Decimal("0.00"))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} – {self.line_total:,.2f}"



# ============================================================================
# FINAL CORPORATE INVOICES
# These documents reconcile existing company-billed charges. Issuing an invoice
# does not post revenue or receivables a second time.
# ============================================================================

class CompanyInvoice(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("ISSUED", "Issued"),
        ("VOID", "Void"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="company_invoices")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="invoices")
    group_reservation = models.ForeignKey(
        GroupReservation, on_delete=models.PROTECT, related_name="company_invoices"
    )
    invoice_number = models.CharField(max_length=40)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="DRAFT")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    company_name_snapshot = models.CharField(max_length=200)
    company_address_snapshot = models.TextField(blank=True)
    company_contact_snapshot = models.CharField(max_length=150, blank=True)
    company_phone_snapshot = models.CharField(max_length=50, blank=True)
    company_email_snapshot = models.EmailField(blank=True)
    group_reference_snapshot = models.CharField(max_length=40)
    group_name_snapshot = models.CharField(max_length=200, blank=True)
    stay_check_in = models.DateField()
    stay_check_out = models.DateField()
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="company_invoices_created",
    )
    issued_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="company_invoices_issued",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "invoice_number"],
                name="unique_company_invoice_number_per_hotel",
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0) & models.Q(total_amount__gte=0),
                name="company_invoice_totals_nonnegative",
            ),
        ]

    def clean(self):
        if self.company_id and self.hotel_id and self.company.hotel_id != self.hotel_id:
            raise ValidationError("Company invoice and company must belong to the same hotel.")
        if self.group_reservation_id:
            if self.group_reservation.hotel_id != self.hotel_id:
                raise ValidationError("Group reservation must belong to the invoice hotel.")
            if self.group_reservation.company_id != self.company_id:
                raise ValidationError("Group reservation must belong to the invoice company.")
        if self.due_date and self.due_date < self.issue_date:
            raise ValidationError("Invoice due date cannot be before its issue date.")

    @property
    def deposit_applied(self):
        return self.deposit_applications.filter(
            transaction_type="APPLIED"
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    @property
    def direct_payments_total(self):
        return self.payments.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    @property
    def amount_paid(self):
        return self.deposit_applied + self.direct_payments_total

    @property
    def balance_due(self):
        return max(self.total_amount - self.amount_paid, Decimal("0.00"))

    def __str__(self):
        return f"Invoice {self.invoice_number} – {self.company_name_snapshot}"


class CompanyInvoiceCharge(models.Model):
    invoice = models.ForeignKey(
        CompanyInvoice, on_delete=models.PROTECT, related_name="charge_lines"
    )
    charge = models.OneToOneField(
        Charge, on_delete=models.PROTECT, related_name="company_invoice_link"
    )
    description_snapshot = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="company_invoice_charge_amount_positive",
            ),
        ]

    def clean(self):
        if self.charge_id and self.invoice_id:
            charge = self.charge
            invoice = self.invoice
            if charge.billing_party != "COMPANY":
                raise ValidationError("Only company-billed charges can be placed on a company invoice.")
            if charge.folio.hotel_id != invoice.hotel_id:
                raise ValidationError("Charge and invoice must belong to the same hotel.")
            if not charge.group_room_allocation_id or charge.group_room_allocation.group_reservation_id != invoice.group_reservation_id:
                raise ValidationError("Charge must belong to the invoice's group reservation.")
            if invoice.status != "DRAFT":
                raise ValidationError("Charge lines can only be added to a draft company invoice.")
            if self.amount is not None and self.amount != charge.amount:
                raise ValidationError("Invoice charge amount must match the source charge amount.")

    def __str__(self):
        return f"{self.description_snapshot} – ₦{self.amount:,.2f}"


class CompanyInvoicePayment(models.Model):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("POS", "POS"),
        ("TRANSFER", "Bank Transfer"),
    )

    invoice = models.ForeignKey(
        CompanyInvoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=12, choices=METHOD_CHOICES)
    bank_account = models.ForeignKey(
        "accounting.BankAccount", null=True, blank=True,
        on_delete=models.PROTECT, related_name="company_invoice_payments",
    )
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="company_invoice_payments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="company_invoice_payment_amount_positive",
            ),
        ]

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError("Company invoice payment must be greater than zero.")
        if self.invoice_id and self.invoice.status != "ISSUED":
            raise ValidationError("Payments can only be recorded against an issued company invoice.")
        if self.bank_account_id and self.invoice_id:
            if self.bank_account.hotel_id != self.invoice.hotel_id or self.bank_account.account.hotel_id != self.invoice.hotel_id:
                raise ValidationError("Payment bank account must belong to the invoice hotel.")

    def __str__(self):
        return f"{self.method} – ₦{self.amount:,.2f} ({self.invoice.invoice_number})"


# ============================================================================
# CORPORATE EVENTS
# ============================================================================
class EventBooking(models.Model):
    """Commercial and operational master record for a hotel event."""

    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("AGREED", "Agreed"),
        ("RELEASED", "Released to Operations"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="event_bookings")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="event_bookings")
    group_reservation = models.ForeignKey(
        GroupReservation, on_delete=models.PROTECT, null=True, blank=True,
        related_name="event_bookings",
    )
    reference = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    event_type = models.CharField(max_length=80, blank=True)
    event_date = models.DateField()
    service_time = models.TimeField()
    location = models.CharField(max_length=200, blank=True)
    expected_participants = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="DRAFT", db_index=True)
    agreement_notes = models.TextField(blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="events_released",
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="events_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event_date", "service_time", "id"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "reference"], name="unique_event_reference_per_hotel"),
            models.CheckConstraint(condition=models.Q(expected_participants__gte=0), name="event_participant_count_nonnegative"),
        ]

    def clean(self):
        if self.company_id and self.hotel_id and self.company.hotel_id != self.hotel_id:
            raise ValidationError("Event company must belong to the event hotel.")
        if self.group_reservation_id:
            if self.group_reservation.hotel_id != self.hotel_id:
                raise ValidationError("Linked group reservation must belong to the event hotel.")
            if self.group_reservation.company_id != self.company_id:
                raise ValidationError("Event company must match the linked group reservation company.")
            if self.group_reservation.status == "CANCELLED":
                raise ValidationError("An event cannot be linked to a cancelled group reservation.")
        if self.status in ("AGREED", "RELEASED", "COMPLETED") and not self.agreement_notes.strip():
            raise ValidationError({"agreement_notes": "Record the agreed commercial terms before approval."})
        if self.status in ("RELEASED", "COMPLETED") and not self.released_at:
            raise ValidationError({"released_at": "Released events must have a release timestamp."})

    def release_to_operations(self, user):
        """Release only an agreed event with a confirmed attendee list."""
        from django.db import transaction
        from django.utils import timezone

        with transaction.atomic():
            event = self.__class__.objects.select_for_update().get(pk=self.pk)
            if event.status != "AGREED":
                raise ValidationError("Only agreed events can be released to operations.")
            if not event.order_lines.exists():
                raise ValidationError("Add at least one food or beverage line before releasing the event.")
            if not event.participants.exists():
                raise ValidationError("Add or confirm the event participants before release.")
            if event.participants.filter(participant_type="IN_HOUSE").exclude(
                room_allocation__status="CHECKED_IN"
            ).exists():
                raise ValidationError("Every in-house participant must be checked in before release.")
            event.status = "RELEASED"
            event.released_at = timezone.now()
            event.released_by = user
            event.full_clean()
            event.save(update_fields=["status", "released_at", "released_by", "updated_at"])
            self.refresh_from_db()
        return self

    def __str__(self):
        return f"{self.reference} – {self.name}"


class EventParticipant(models.Model):
    PARTICIPANT_TYPES = (
        ("IN_HOUSE", "In-house guest"),
        ("EXTERNAL", "External participant"),
    )

    event = models.ForeignKey(EventBooking, on_delete=models.CASCADE, related_name="participants")
    participant_type = models.CharField(max_length=12, choices=PARTICIPANT_TYPES)
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT, null=True, blank=True, related_name="event_participations")
    room_allocation = models.ForeignKey(
        GroupReservationRoom, on_delete=models.PROTECT, null=True, blank=True,
        related_name="event_participations",
    )
    external_name = models.CharField(max_length=150, blank=True)
    external_organization = models.CharField(max_length=150, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["participant_type", "id"]

    def clean(self):
        if not self.event_id:
            return
        if self.participant_type == "IN_HOUSE":
            if not self.guest_id or not self.room_allocation_id:
                raise ValidationError("In-house participants require a guest and group room allocation.")
            if self.room_allocation.group_reservation.hotel_id != self.event.hotel_id:
                raise ValidationError("Participant room allocation belongs to another hotel.")
            if self.room_allocation.guest_id != self.guest_id:
                raise ValidationError("Participant guest must match the selected room allocation.")
            if self.room_allocation.group_reservation_id != self.event.group_reservation_id:
                raise ValidationError("In-house participant must belong to the event's linked group reservation.")
            if self.external_name:
                raise ValidationError("Do not enter an external name for an in-house participant.")
        elif self.participant_type == "EXTERNAL":
            if not self.external_name.strip():
                raise ValidationError({"external_name": "External participant name is required."})
            if self.guest_id or self.room_allocation_id:
                raise ValidationError("External participants must not be linked to a hotel guest or room.")
        else:
            raise ValidationError({"participant_type": "Select a valid participant type."})

    @property
    def display_name(self):
        return self.guest.full_name if self.guest_id else self.external_name

    @property
    def room_number(self):
        return self.room_allocation.room.room_number if self.room_allocation_id and self.room_allocation.room_id else ""


class EventOrderLine(models.Model):
    LINE_TYPES = (("FOOD", "Food"), ("BEVERAGE", "Beverage"), ("OTHER", "Other"))

    event = models.ForeignKey(EventBooking, on_delete=models.CASCADE, related_name="order_lines")
    line_type = models.CharField(max_length=10, choices=LINE_TYPES)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="event_line_quantity_positive"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="event_line_unit_price_nonnegative"),
        ]

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError({"unit_price": "Unit price cannot be negative."})
        if self.event_id and self.event.status not in ("DRAFT", "AGREED"):
            raise ValidationError("Event order lines cannot be changed after release or cancellation.")

    @property
    def total_price(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.description} × {self.quantity}"
