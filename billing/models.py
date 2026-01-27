from django.db import models
from django.conf import settings
from inventory.models import Department
# from rooms.models import Room
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.utils import timezone


User = settings.AUTH_USER_MODEL


class Guest(models.Model):
    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
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
        if self.folio_type != "ROOM":
            return

        if not self.room or not self.room.category:
            raise ValidationError("Room or category missing.")

        rate = getattr(self.room.category, "rate", None)

        if not rate or not rate.is_active:
            raise ValidationError("No active rate for this room category.")

        nights = self.nights
        total = Decimal(rate.price_per_night) * nights

        Charge.objects.create(
            folio=self,
            description=f"Room charge ({nights} night(s))",
            department=Department.objects.get(name__iexact="Frontdesk"),
            amount=total,
            reference=f"ROOM-{self.room.room_number}"
        )

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
        if self.folio_type != "ROOM" or self.is_closed:
            return

        if not self.room or not self.room.category:
            raise ValidationError("Room or category missing.")

        rate = getattr(self.room.category, "rate", None)
        if not rate or not rate.is_active:
            raise ValidationError("No active rate for this room category.")

        today = timezone.now().date()

        # ❌ Already charged today
        if self.last_room_charge_date == today:
            return

        Charge.objects.create(
            folio=self,
            description=f"Room charge – {today}",
            department=Department.objects.get(name__iexact="Frontdesk"),
            amount=Decimal(rate.price_per_night),
            reference=f"ROOM-{self.room.room_number}-{today}"
        )

        self.last_room_charge_date = today
        self.save(update_fields=["last_room_charge_date"])

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
        return cls.objects.filter(
            folio_type="ROOM",
            room=room,
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


# =========================
# PAYMENT (SETTLEMENT)
# =========================
class Payment(models.Model):
    METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("POS", "POS"),
        ("TRANSFER", "Bank Transfer"),
    )

    folio = models.ForeignKey(
        Folio,
        related_name="payments",
        on_delete=models.PROTECT
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
