from django.db import models
from django.conf import settings
from rooms.models import Room
from django.utils import timezone
from inventory.models import Hotel, Product


class CleaningLog(models.Model):

    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name="cleaning_logs"
    )

    cleaned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    cleaned_at = models.DateTimeField(auto_now_add=True)

    previous_status = models.CharField(max_length=50)

    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Room {self.room.room_number} cleaned at {self.cleaned_at}"


class CleaningAssignment(models.Model):

    STATUS_CHOICES = (
        ("ASSIGNED", "Assigned"),
        ("IN_PROGRESS", "In Progress"),
        ("DONE", "Done"),
        ("INSPECTION", "Waiting Inspection"),
    )

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="cleaning_assignments"
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_cleanings"
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_assignments"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ASSIGNED"
    )
    notes = models.TextField(
        blank=True,
    )

    assigned_at = models.DateTimeField(
        auto_now_add=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    inspected_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    inspected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inspected_cleanings",
    )


    def __str__(self):
        return f"Room {self.room.room_number} → {self.assigned_to}"

class CleaningMaterialUsage(models.Model):

    assignment = models.ForeignKey(
        CleaningAssignment,
        on_delete=models.PROTECT,
        related_name="material_usages",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    notes = models.TextField(
        blank=True,
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="cleaning_material_usages",
    )

    recorded_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return (
            f"Room {self.assignment.room.room_number} - "
            f"{self.product.name} - "
            f"{self.quantity}"
        )
    

class LostFoundItem(models.Model):

    STATUS_CHOICES = (
        ("FOUND", "Found"),
        ("CLAIMED", "Claimed"),
        ("DISPOSED", "Disposed"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="lost_found_items",
    )

    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lost_found_items",
    )

    description = models.TextField()

    found_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="found_items",
    )

    found_at = models.DateTimeField(
        default=timezone.now,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="FOUND",
    )

    claimed_by = models.CharField(
        max_length=200,
        blank=True,
    )

    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Lost Item - {self.hotel.name} - Room {self.room}"