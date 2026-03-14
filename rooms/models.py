from django.db import models
from inventory.models import Hotel

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
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="buildings"
    )

    name = models.CharField(max_length=100)

    is_active = models.BooleanField(
        default=True,
        help_text="Inactive buildings cannot be used for new rooms"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("hotel", "name")

    def __str__(self):
        return f"{self.name} ({self.hotel.name})"
    

class Room(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="rooms"
    )

    room_number = models.CharField(max_length=10)

    category = models.ForeignKey(
        RoomCategory,
        on_delete=models.PROTECT,
        related_name="rooms"
    )

    building = models.ForeignKey(
        Building,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rooms"
    )

    floor = models.ForeignKey(
        "Floor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rooms"
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

    class Meta:
        unique_together = ("hotel", "room_number")
        ordering = ["room_number"]

    def __str__(self):
        return f"{self.hotel.name} - Room {self.room_number}"

    def refresh_status(self):
        from billing.models import Folio

        has_active_folio = Folio.objects.filter(
            folio_type="ROOM",
            room=self,
            hotel=self.hotel,
            is_closed=False
        ).exists()

        if has_active_folio:
            self.status = "OCCUPIED"
        else:
            if self.status == "OCCUPIED":
                self.status = "VACANT_DIRTY"

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
    

class Floor(models.Model):

    building = models.ForeignKey(
        Building,
        on_delete=models.CASCADE,
        related_name="floors"
    )

    name = models.CharField(
        max_length=50,
        help_text="Example: Floor 1, Ground Floor"
    )

    number = models.PositiveIntegerField()

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["number"]
        unique_together = ("building", "number")

    def __str__(self):
        return f"{self.building.name} - {self.name}"