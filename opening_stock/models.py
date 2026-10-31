from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from inventory.models import Department, Hotel, Product


class OpeningStockBatch(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("POSTED", "Posted"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="opening_stock_batches")
    reference = models.CharField(max_length=60)
    opening_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="DRAFT")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opening_stock_batches_created")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="opening_stock_batches_posted")
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.OneToOneField("accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="opening_stock_batch")

    class Meta:
        ordering = ["-opening_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "reference"], name="unique_opening_batch_reference_per_hotel"),
        ]

    def __str__(self):
        return f"{self.hotel} - {self.reference} ({self.status})"


class OpeningStockLine(models.Model):
    LINE_TYPES = (
        ("STOCK", "Department stock"),
        ("LINEN", "Linen"),
        ("EQUIPMENT", "Equipment / fixed asset"),
    )
    LINEN_LOCATIONS = (
        ("STORE", "Linen Store"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("LAUNDRY", "Laundry"),
    )
    CONDITIONS = (
        ("CLEAN", "Clean"),
        ("DIRTY", "Dirty"),
    )

    batch = models.ForeignKey(OpeningStockBatch, on_delete=models.CASCADE, related_name="lines")
    line_type = models.CharField(max_length=12, choices=LINE_TYPES)

    # Department stock, quantity and unit cost are in the product BASE UNIT.
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="opening_stock_lines")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, null=True, blank=True, related_name="opening_stock_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0.000"))
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    # Linen is tracked by location and condition in its own transaction ledger.
    linen_item = models.ForeignKey("linen.LinenItem", on_delete=models.PROTECT, null=True, blank=True, related_name="opening_stock_lines")
    linen_location = models.CharField(max_length=20, choices=LINEN_LOCATIONS, blank=True)
    linen_condition = models.CharField(max_length=10, choices=CONDITIONS, blank=True)

    # Existing equipment is individually registered, one asset per line.
    asset_name = models.CharField(max_length=150, blank=True)
    asset_product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="opening_equipment_lines")
    room = models.ForeignKey("rooms.Room", on_delete=models.PROTECT, null=True, blank=True, related_name="opening_equipment_lines")
    maintenance_location = models.ForeignKey("maintenance.MaintenanceLocation", on_delete=models.PROTECT, null=True, blank=True, related_name="opening_equipment_lines")
    serial_number = models.CharField(max_length=100, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    model_number = models.CharField(max_length=100, blank=True)
    ASSET_CONDITIONS = (
        ("NEW", "New"),
        ("GOOD", "Good"),
        ("FAIR", "Fair"),
        ("POOR", "Poor"),
        ("DAMAGED", "Damaged"),
    )
    asset_condition = models.CharField(max_length=12, choices=ASSET_CONDITIONS, blank=True)
    asset_purchase_date = models.DateField(null=True, blank=True)
    asset_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    @property
    def line_value(self):
        if self.line_type == "STOCK":
            return self.quantity * self.unit_cost
        if self.line_type == "LINEN":
            return Decimal(self.quantity) * self.unit_cost
        return self.asset_value

    def clean(self):
        if self.line_type == "STOCK":
            if not self.product_id or not self.department_id:
                raise ValidationError("Department stock lines require a product and department.")
            if self.linen_item_id or self.asset_product_id or self.asset_name:
                raise ValidationError("Stock lines cannot contain linen or equipment details.")
            if self.quantity <= 0 or self.unit_cost <= 0:
                raise ValidationError("Stock quantity and unit cost must be greater than zero.")
            if self.product.hotel_id != self.batch.hotel_id or self.department.hotel_id != self.batch.hotel_id:
                raise ValidationError("Stock product and department must belong to the batch hotel.")
            if self.product.product_type in ("EQUIPMENT", "SERVICE") or self.product.usage_type == "ASSET":
                raise ValidationError("Equipment and asset products must use the appropriate opening category.")
        elif self.line_type == "LINEN":
            if not self.linen_item_id or not self.linen_location or not self.linen_condition:
                raise ValidationError("Linen lines require an item, location and condition.")
            if self.quantity <= 0 or self.quantity != self.quantity.to_integral_value() or self.unit_cost <= 0:
                raise ValidationError("Linen quantity must be a positive whole number and unit cost must be greater than zero.")
            if self.linen_item.product.hotel_id != self.batch.hotel_id:
                raise ValidationError("Linen item must belong to the batch hotel.")
            if self.linen_item.product.usage_type != "ASSET":
                raise ValidationError("Linen products must use the ASSET usage type.")
            if self.product_id or self.department_id or self.asset_product_id or self.asset_name:
                raise ValidationError("Linen lines cannot contain stock or equipment details.")
        elif self.line_type == "EQUIPMENT":
            if not self.asset_name.strip() or self.asset_value <= 0:
                raise ValidationError("Equipment lines require a name and positive opening value.")
            if self.room_id and self.maintenance_location_id:
                raise ValidationError("Choose either a room or a maintenance location, not both.")
            if self.asset_product_id and (
                self.asset_product.hotel_id != self.batch.hotel_id
                or self.asset_product.product_type != "EQUIPMENT"
            ):
                raise ValidationError("Equipment product must be an equipment product from this hotel.")
            if self.room_id and self.room.hotel_id != self.batch.hotel_id:
                raise ValidationError("Room must belong to the batch hotel.")
            if self.maintenance_location_id and self.maintenance_location.hotel_id != self.batch.hotel_id:
                raise ValidationError("Maintenance location must belong to the batch hotel.")
            if self.product_id or self.department_id or self.linen_item_id:
                raise ValidationError("Equipment lines cannot contain stock or linen details.")
        else:
            raise ValidationError("Select a valid opening line type.")
