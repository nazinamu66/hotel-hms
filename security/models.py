from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from inventory.models import Hotel
from maintenance.models import MaintenanceAsset


class PropertyExitPass(models.Model):
    TEMPORARY = "TEMPORARY"
    PERMANENT = "PERMANENT"
    PASS_TYPES = ((TEMPORARY, "Temporary removal — return required"), (PERMANENT, "Permanent release"))

    AUTHORIZED = "AUTHORIZED"
    OUT = "OUT"
    RETURNED = "RETURNED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = (
        (AUTHORIZED, "Authorised — awaiting gate exit"),
        (OUT, "Outside hotel"),
        (RETURNED, "Returned"),
        (RELEASED, "Permanently released"),
        (CANCELLED, "Cancelled"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="property_exit_passes")
    pass_number = models.CharField(max_length=30, unique=True, blank=True)
    pass_type = models.CharField(max_length=12, choices=PASS_TYPES)
    staff_name = models.CharField(max_length=160)
    staff_department = models.CharField(max_length=120, blank=True)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="property_exit_passes")
    item_is_equipment = models.BooleanField(default=False, help_text="Tick this when the item is equipment. A serial number is optional.")
    equipment_asset = models.ForeignKey(MaintenanceAsset, on_delete=models.PROTECT, null=True, blank=True, related_name="exit_passes")
    item_description = models.CharField(max_length=250)
    serial_number = models.CharField(max_length=120, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    purpose = models.TextField()
    expected_return_date = models.DateField(null=True, blank=True)
    authorising_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authorised_property_exits")
    authorised_at = models.DateTimeField(default=timezone.now)
    approval_reference = models.CharField(max_length=100, blank=True)
    authorisation_note = models.FileField(upload_to="security/property-authorisations/%Y/%m/", null=True, blank=True)
    staff_acknowledgement = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=AUTHORIZED)
    gate_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_gate_exits")
    gate_out_at = models.DateTimeField(null=True, blank=True)
    gate_out_note = models.TextField(blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_property_returns")
    return_condition = models.CharField(max_length=200, blank=True)
    return_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["hotel", "status"]), models.Index(fields=["hotel", "pass_type", "created_at"])]

    def clean(self):
        errors = {}
        if self.pass_type == self.TEMPORARY and not self.expected_return_date:
            errors["expected_return_date"] = "An expected return date is required for temporary removals."
        if self.pass_type == self.PERMANENT and self.expected_return_date:
            errors["expected_return_date"] = "Permanent releases do not have a return date."
        if not self.approval_reference and not self.authorisation_note:
            errors["approval_reference"] = "Record the handwritten note reference or attach a scan/photo of the management authorisation."
        if self.equipment_asset_id:
            if not self.item_is_equipment:
                errors["equipment_asset"] = "Select 'This item is equipment' to link a registered asset."
            elif self.equipment_asset.hotel_id != self.hotel_id:
                errors["equipment_asset"] = "The equipment must belong to the same hotel."
            elif self.serial_number and self.equipment_asset.serial_number and self.serial_number != self.equipment_asset.serial_number:
                errors["serial_number"] = "The serial number does not match the selected equipment asset."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.pass_number:
            self.pass_number = f"PE-{self.hotel_id}-{self.pk:07d}"
            super().save(update_fields=["pass_number"])

    def __str__(self):
        return f"{self.pass_number or 'New pass'} — {self.item_description}"
