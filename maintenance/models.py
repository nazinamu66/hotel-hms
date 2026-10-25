from django.db import models
from django.conf import settings
from rooms.models import Room
from decimal import Decimal
from inventory.models import Hotel, Supplier, Product


class MaintenanceLocation(models.Model):
    """A non-room physical area within a hotel that can have maintenance issues."""

    LOCATION_TYPES = (
        ("RECEPTION", "Reception"),
        ("LOBBY", "Lobby"),
        ("KITCHEN", "Kitchen"),
        ("RESTAURANT", "Restaurant / Dining Area"),
        ("LAUNDRY", "Laundry"),
        ("CORRIDOR", "Hallway / Corridor"),
        ("STAIRWELL", "Stairwell"),
        ("GROUNDS", "Grounds"),
        ("PARKING", "Parking"),
        ("GATE", "Gate / Entrance"),
        ("OFFICE", "Office"),
        ("STORE", "Store / Warehouse"),
        ("GYM", "Gym"),
        ("PUBLIC_AREA", "Other Public Area"),
        ("PLANT_ROOM", "Plant / Utility Room"),
        ("OTHER", "Other"),
    )

    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("INACTIVE", "Inactive"),
        ("UNDER_MAINTENANCE", "Under Maintenance"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="maintenance_locations",
    )
    name = models.CharField(max_length=150)
    location_type = models.CharField(max_length=30, choices=LOCATION_TYPES, default="OTHER")
    description = models.TextField(blank=True)
    responsible_departments = models.ManyToManyField(
        "inventory.Department",
        blank=True,
        related_name="maintenance_locations",
    )
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default="ACTIVE")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["hotel__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_maintenance_location_per_hotel",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.hotel})"


class MaintenanceTicket(models.Model):

    PRIORITY_CHOICES = (
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
    )

    STATUS_CHOICES = (
        ("OPEN", "Open"),
        ("IN_PROGRESS", "In Progress"),
        ("RESOLVED", "Resolved"),
    )

    COMPLETION_CHOICES = (
        ("ROOM_READY", "Room Ready"),
        (
            "HOUSEKEEPING_REQUIRED",
            "Housekeeping Required",
        ),
        (
            "NOT_APPLICABLE",
            "Room Not Affected",
        ),
    )

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="maintenance_tickets",
    )

    location = models.ForeignKey(
        "MaintenanceLocation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="maintenance_tickets",
    )
    
    schedule = models.ForeignKey(
        "MaintenanceSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    description = models.TextField()

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reported_maintenance",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_maintenance",
    )

    room_status_before_maintenance = models.CharField(
        max_length=20,
        choices=Room.STATUS_CHOICES,
        null=True,
        blank=True,
    )

    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default="MEDIUM",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="OPEN",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    resolution_note = models.TextField(
        blank=True,
        default="",
    )

    completion_outcome = models.CharField(
        max_length=30,
        choices=COMPLETION_CHOICES,
        blank=True,
        default="",
    )
    @property
    def material_total(self):
        return sum(
            (
                material.total_cost
                for material in self.material_entries.all()
            ),
            Decimal("0.00"),
        )

    @property
    def labour_total(self):
        return sum(
            (
                labour.amount
                for labour in self.labour_entries.all()
            ),
            Decimal("0.00"),
        )

    @property
    def total_maintenance_cost(self):
        return (
            self.material_total
            + self.labour_total
        )

    def __str__(self):
        if self.room_id:
            target = f"Room {self.room.room_number}"
        elif self.location_id:
            target = self.location.name
        else:
            target = "Unspecified location"
        return f"{target} - {self.status}"

    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.room_id) == bool(self.location_id):
            raise ValidationError(
                "A maintenance ticket must reference either a room or a non-room location, not both."
            )
    
class MaintenanceWorkNote(models.Model):

    ticket = models.ForeignKey(
        MaintenanceTicket,
        on_delete=models.CASCADE,
        related_name="work_notes",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="maintenance_work_notes",
    )

    note = models.TextField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return (
            f"Work note for Maintenance Ticket "
            f"#{self.ticket_id}"
        )

class MaintenanceLabour(models.Model):

    LABOUR_TYPE_CHOICES = (
        ("INTERNAL", "Internal"),
        ("OUTSOURCED", "Outsourced"),
    )

    ticket = models.ForeignKey(
        MaintenanceTicket,
        on_delete=models.CASCADE,
        related_name="labour_entries",
    )

    labour_type = models.CharField(
        max_length=20,
        choices=LABOUR_TYPE_CHOICES,
    )

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_labour",
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="maintenance_jobs",
    )

    description = models.TextField()

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_maintenance_labour",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

class MaintenanceMaterial(models.Model):

    SOURCE_CHOICES = (
        ("STORE", "From Maintenance Store"),
        ("DIRECT", "Direct Purchase"),
    )

    ticket = models.ForeignKey(
        MaintenanceTicket,
        on_delete=models.CASCADE,
        related_name="material_entries",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="maintenance_materials",
    )

    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="maintenance_materials",
    )

    description = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_maintenance_materials",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return (
            f"{self.ticket_id} - "
            f"{self.product.name} - "
            f"{self.quantity}"
        )

# ============================================================
# MAINTENANCE ASSET
# ============================================================

class MaintenanceAsset(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="maintenance_assets",
    )

    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="maintenance_assets",
    )

    name = models.CharField(
        max_length=150,
    )

    asset_type = models.CharField(
        max_length=100,
        blank=True,
    )

    serial_number = models.CharField(
        max_length=100,
        blank=True,
    )

    manufacturer = models.CharField(
        max_length=100,
        blank=True,
    )

    model_number = models.CharField(
        max_length=100,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):

        if self.room_id:
            return f"{self.name} - Room {self.room.room_number}"
        if self.location_id:
            return f"{self.name} - {self.location.name}"
        return self.name


# ============================================================
# PREVENTIVE MAINTENANCE SCHEDULE
# ============================================================

class MaintenanceSchedule(models.Model):

    asset = models.ForeignKey(
        MaintenanceAsset,
        on_delete=models.CASCADE,
        related_name="schedules",
    )

    name = models.CharField(
        max_length=150,
    )

    interval_days = models.PositiveIntegerField(
        help_text="Number of days between maintenance activities.",
    )

    next_due_date = models.DateField()

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["next_due_date", "name"]

    def __str__(self):
        return (
            f"{self.asset.name} - "
            f"{self.name}"
        )