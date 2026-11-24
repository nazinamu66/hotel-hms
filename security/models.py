from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
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


class VehicleVisit(models.Model):
    COMMON_MAKES = [
        ("TOYOTA", "Toyota"), ("HONDA", "Honda"), ("LEXUS", "Lexus"),
        ("MERCEDES_BENZ", "Mercedes-Benz"), ("NISSAN", "Nissan"),
        ("HYUNDAI", "Hyundai"), ("KIA", "Kia"), ("FORD", "Ford"),
        ("VOLKSWAGEN", "Volkswagen"), ("PEUGEOT", "Peugeot"),
        ("MITSUBISHI", "Mitsubishi"), ("MAZDA", "Mazda"),
        ("BMW", "BMW"), ("AUDI", "Audi"), ("VOLVO", "Volvo"),
        ("LAND_ROVER", "Land Rover"), ("RANGE_ROVER", "Range Rover"),
        ("SUZUKI", "Suzuki"), ("SUBARU", "Subaru"), ("OTHER", "Other"),
    ]

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="security_vehicle_visits")
    make = models.CharField(max_length=30, choices=COMMON_MAKES)
    other_make = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    plate_number = models.CharField(max_length=30)
    entered_at = models.DateTimeField(default=timezone.now, editable=False)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_vehicle_entries")
    entry_note = models.TextField(blank=True)
    exited_at = models.DateTimeField(null=True, blank=True)
    exited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_vehicle_exits")
    exit_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entered_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "plate_number"],
                condition=Q(exited_at__isnull=True),
                name="uniq_inside_vehicle_plate_per_hotel",
            )
        ]
        indexes = [
            models.Index(fields=["hotel", "exited_at"]),
            models.Index(fields=["hotel", "entered_at"]),
        ]

    def clean(self):
        if self.make == "OTHER" and not self.other_make.strip():
            raise ValidationError({"other_make": "Enter the vehicle make when Other is selected."})
        if self.make != "OTHER" and self.other_make:
            self.other_make = ""

    def save(self, *args, **kwargs):
        self.plate_number = " ".join(self.plate_number.upper().split())
        if self.make != "OTHER":
            self.other_make = ""
        super().save(*args, **kwargs)

    @property
    def display_make(self):
        return self.other_make if self.make == "OTHER" else self.get_make_display()

    @property
    def is_inside(self):
        return self.exited_at is None

    def __str__(self):
        return f"{self.plate_number} — {self.display_make}"


class VisitorVisit(models.Model):
    GUEST = 'GUEST'
    STAFF = 'STAFF'
    OUTSOURCED = 'OUTSOURCED'
    VISITOR_TYPES = ((GUEST, 'Visiting a hotel guest'), (STAFF, 'Visiting a staff member'), (OUTSOURCED, 'Outsourced labour / contractor'))

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name='security_visitor_visits')
    visitor_type = models.CharField(max_length=12, choices=VISITOR_TYPES)
    visitor_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=30, blank=True)
    id_type = models.CharField(max_length=50, blank=True)
    id_number = models.CharField(max_length=100, blank=True)
    purpose = models.TextField()
    vehicle_plate = models.CharField(max_length=30, blank=True)
    guest_name = models.CharField(max_length=160, blank=True)
    guest_room_number = models.CharField(max_length=20, blank=True)
    host_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='hosted_security_visits')
    host_staff_name = models.CharField(max_length=160, blank=True)
    host_department = models.CharField(max_length=120, blank=True)
    maintenance_ticket = models.ForeignKey('maintenance.MaintenanceTicket', on_delete=models.PROTECT, null=True, blank=True, related_name='security_visitor_visits')
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.PROTECT, null=True, blank=True, related_name='security_visitor_visits')
    work_description = models.CharField(max_length=250, blank=True)
    entered_at = models.DateTimeField(default=timezone.now, editable=False)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='security_visitor_entries')
    entry_note = models.TextField(blank=True)
    exited_at = models.DateTimeField(null=True, blank=True)
    exited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='security_visitor_exits')
    exit_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-entered_at', '-pk']
        indexes = [models.Index(fields=['hotel', 'exited_at']), models.Index(fields=['hotel', 'entered_at'])]

    def clean(self):
        errors = {}
        if self.visitor_type == self.GUEST and not self.guest_name.strip():
            errors['guest_name'] = 'Enter the name of the guest being visited.'
        if self.visitor_type == self.STAFF:
            if not self.host_staff_id and not self.host_staff_name.strip():
                errors['host_staff_name'] = 'Select or enter the staff member being visited.'
            if self.host_staff_id and self.host_staff.hotel_id != self.hotel_id and not (self.host_staff.department_id and self.host_staff.department.hotel_id == self.hotel_id):
                errors['host_staff'] = 'The selected staff member belongs to another hotel.'
        if self.visitor_type == self.OUTSOURCED:
            if not self.work_description.strip() and not self.maintenance_ticket_id:
                errors['work_description'] = 'Describe the outsourced work or select a Maintenance work order.'
            if self.maintenance_ticket_id:
                ticket_hotel_id = self.maintenance_ticket.room.hotel_id if self.maintenance_ticket.room_id else self.maintenance_ticket.location.hotel_id if self.maintenance_ticket.location_id else None
                if ticket_hotel_id != self.hotel_id:
                    errors['maintenance_ticket'] = 'The selected work order belongs to another hotel.'
            if self.supplier_id and self.supplier.hotel_id != self.hotel_id:
                errors['supplier'] = 'The selected supplier belongs to another hotel.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.vehicle_plate = ' '.join(self.vehicle_plate.upper().split())
        if self.host_staff_id:
            self.host_staff_name = self.host_staff.get_full_name() or self.host_staff.username
            self.host_department = self.host_staff.department.name if self.host_staff.department_id else self.host_department
        super().save(*args, **kwargs)

    @property
    def is_inside(self):
        return self.exited_at is None

    @property
    def host_display(self):
        if self.visitor_type == self.GUEST:
            return f'{self.guest_name} · Room {self.guest_room_number}' if self.guest_room_number else self.guest_name
        if self.visitor_type == self.STAFF:
            return f'{self.host_staff_name} · {self.host_department}' if self.host_department else self.host_staff_name
        if self.supplier_id:
            return self.supplier.name
        if self.work_description:
            return self.work_description
        return self.maintenance_ticket.description[:100] if self.maintenance_ticket_id else 'Outsourced work'

    def __str__(self):
        return f'{self.visitor_name} — {self.get_visitor_type_display()}'
