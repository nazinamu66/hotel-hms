from django.db import models


class RoomCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    @property
    def nightly_rate(self):
        if hasattr(self, "rate") and self.rate.is_active:
            return self.rate.price_per_night
        return None

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Building(models.Model):
    name = models.CharField(max_length=100, unique=True)

    is_active = models.BooleanField(
        default=True,
        help_text="Inactive buildings cannot be used for new rooms"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Room(models.Model):
    room_number = models.CharField(max_length=10, unique=True)

    category = models.ForeignKey(
        RoomCategory,
        on_delete=models.PROTECT,
        related_name="rooms"
    )

    building = models.ForeignKey(
        Building,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    STATUS_CHOICES = (
        ("AVAILABLE", "Available"),
        ("OCCUPIED", "Occupied"),
        ("OCCUPIED_DIRTY", "Occupied – Needs Cleaning"),
        ("VACANT_DIRTY", "Vacant – Needs Cleaning"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="AVAILABLE"
    )

    def __str__(self):
        return f"Room {self.room_number}"

    # ✅ ADD THIS METHOD
    def refresh_status(self):
        from billing.models import Folio  # lazy import (prevents circular import)

        has_active_folio = Folio.objects.filter(
            folio_type="ROOM",
            room=self,
            is_closed=False
        ).exists()

        if has_active_folio:
            self.status = "OCCUPIED"
        else:
            if self.status == "OCCUPIED":
                self.status = "VACANT_DIRTY"
            # DO NOT auto-clear VACANT_DIRTY

        self.save(update_fields=["status"])


from django.db import models
from decimal import Decimal

class RoomRate(models.Model):
    category = models.OneToOneField(
        "rooms.RoomCategory",
        on_delete=models.CASCADE,
        related_name="rate"
    )

    price_per_night = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    currency = models.CharField(
        max_length=10,
        default="NGN"
    )


    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__name"]

    def __str__(self):
        return f"{self.category.name} – {self.price_per_night} {self.currency}"
