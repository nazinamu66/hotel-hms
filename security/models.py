from datetime import time, timedelta
import uuid

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


class SecurityShiftConfiguration(models.Model):
    """Hotel-specific duty pattern; defaults reflect CCH but remain configurable."""
    hotel = models.OneToOneField(Hotel, on_delete=models.CASCADE, related_name="security_shift_configuration")
    handover_time = models.TimeField(default=time(8, 0))
    officers_per_team = models.PositiveSmallIntegerField(default=2)
    cso_leaves_at = models.TimeField(default=time(17, 0))
    cso_daily = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.hotel} security duty configuration"


class SecurityShiftHandover(models.Model):
    """Auditable daily handover between outgoing and incoming security teams."""
    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="security_shift_handovers")
    duty_date = models.DateField(default=timezone.localdate)
    outgoing_officer_1 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_handovers_outgoing_1")
    outgoing_officer_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_handovers_outgoing_2")
    incoming_officer_1 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_handovers_incoming_1")
    incoming_officer_2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_handovers_incoming_2")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_handovers_submitted")
    submitted_at = models.DateTimeField(default=timezone.now)
    outgoing_notes = models.TextField(blank=True)
    outstanding_matters = models.TextField(blank=True)
    register_snapshot = models.JSONField(default=dict, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="security_handovers_acknowledged")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    incoming_notes = models.TextField(blank=True)
    is_complete = models.BooleanField(default=False)
    cso_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_handovers_cso_reviewed")
    cso_reviewed_at = models.DateTimeField(null=True, blank=True)
    cso_review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-duty_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "duty_date"], name="uniq_security_handover_hotel_day"),
        ]
        indexes = [models.Index(fields=["hotel", "is_complete", "duty_date"])]

    def clean(self):
        errors = {}
        officers = [self.outgoing_officer_1, self.outgoing_officer_2, self.incoming_officer_1, self.incoming_officer_2]
        ids = [officer.pk for officer in officers if officer and officer.pk]
        if len(ids) != len(set(ids)):
            errors["incoming_officer_2"] = "Each of the four duty positions must be assigned to a different officer."
        for field, officer in zip(
            ("outgoing_officer_1", "outgoing_officer_2", "incoming_officer_1", "incoming_officer_2"), officers
        ):
            if officer and (
                officer.role != "SECURITY"
                or not officer.is_active
                or officer.hotel_id != self.hotel_id
                or not officer.department_id
                or officer.department.department_type != "SECURITY"
                or officer.department.hotel_id != self.hotel_id
            ):
                errors[field] = "Select an active Security officer assigned to this hotel and its Security department."
        if self.submitted_by_id and self.submitted_by_id not in {
            self.outgoing_officer_1_id, self.outgoing_officer_2_id
        }:
            errors["submitted_by"] = "The handover must be submitted by an outgoing officer."
        if self.acknowledged_by_id and self.acknowledged_by_id not in {
            self.incoming_officer_1_id, self.incoming_officer_2_id
        }:
            errors["acknowledged_by"] = "The handover must be acknowledged by an incoming officer."
        if self.cso_reviewed_by_id and not self.cso_reviewed_by.is_security_head:
            errors["cso_reviewed_by"] = "Only the Security Head can record a CSO review."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.acknowledged_at and not self.is_complete:
            self.is_complete = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hotel} security handover — {self.duty_date}"


class StaffAttendance(models.Model):
    """One daily attendance record; approved short movements are tracked separately."""
    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="staff_attendance")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_attendance")
    roster_assignment = models.ForeignKey(
        "personnel.RosterAssignment", on_delete=models.PROTECT,
        null=True, blank=True, related_name="attendance_records",
    )
    scheduled_start_at = models.DateTimeField(null=True, blank=True)
    scheduled_end_at = models.DateTimeField(null=True, blank=True)
    scheduled_shift_name = models.CharField(max_length=80, blank=True)
    grace_minutes_snapshot = models.PositiveSmallIntegerField(default=0)
    work_date = models.DateField(default=timezone.localdate)
    arrived_at = models.DateTimeField(default=timezone.now, editable=False)
    arrival_recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_attendance_arrivals")
    arrival_note = models.TextField(blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    departure_recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_attendance_departures")
    departure_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "-arrived_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["hotel", "employee", "work_date"], name="uniq_staff_attendance_employee_day")]
        indexes = [models.Index(fields=["hotel", "work_date"]), models.Index(fields=["hotel", "departed_at", "work_date"])]

    def clean(self):
        errors = {}
        if self.employee_id:
            employee = self.employee
            employee_hotel_id = employee.hotel_id or (
                employee.department.hotel_id if employee.department_id else None
            )
            if not employee.is_active or employee_hotel_id != self.hotel_id:
                errors["employee"] = "Select an active employee assigned to this hotel."
            if employee.role in {"ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT"}:
                errors["employee"] = "This account is not an employee of the selected hotel."
        if self.arrival_recorded_by_id:
            officer = self.arrival_recorded_by
            officer_hotel_id = officer.hotel_id or (
                officer.department.hotel_id if officer.department_id else None
            )
            if (
                officer.role != "SECURITY" or not officer.is_active
                or officer_hotel_id != self.hotel_id
                or not officer.department_id
                or officer.department.department_type != "SECURITY"
            ):
                errors["arrival_recorded_by"] = "Arrival must be recorded by an active Security officer at this hotel."
        if self.departure_recorded_by_id:
            officer = self.departure_recorded_by
            officer_hotel_id = officer.hotel_id or (
                officer.department.hotel_id if officer.department_id else None
            )
            if (
                officer.role != "SECURITY" or not officer.is_active
                or officer_hotel_id != self.hotel_id
                or not officer.department_id
                or officer.department.department_type != "SECURITY"
            ):
                errors["departure_recorded_by"] = "Departure must be recorded by an active Security officer at this hotel."
        if self.departed_at and not self.departure_recorded_by_id:
            errors["departure_recorded_by"] = "Record the Security officer who confirms departure."
        if self.roster_assignment_id:
            assignment = self.roster_assignment
            if assignment.roster.hotel_id != self.hotel_id:
                errors["roster_assignment"] = "The roster assignment belongs to another hotel."
            if assignment.employee_id != self.employee_id or assignment.work_date != self.work_date:
                errors["roster_assignment"] = "The roster assignment must match this employee and work date."
            if assignment.is_day_off or not assignment.shift_id or assignment.roster.status != "APPROVED":
                errors["roster_assignment"] = "Attendance must be linked to an approved working shift."
            if not self.scheduled_start_at or not self.scheduled_end_at:
                errors["scheduled_start_at"] = "Scheduled shift times must be saved with roster-linked attendance."
        if errors:
            raise ValidationError(errors)

    @property
    def punctuality_status(self):
        if not self.roster_assignment_id or not self.scheduled_start_at:
            return "UNLINKED"
        deadline = self.scheduled_start_at + timedelta(minutes=self.grace_minutes_snapshot)
        return "LATE" if self.arrived_at > deadline else "ON_TIME"

    @property
    def late_minutes_beyond_grace(self):
        if self.punctuality_status != "LATE":
            return 0
        deadline = self.scheduled_start_at + timedelta(minutes=self.grace_minutes_snapshot)
        return max(0, int((self.arrived_at - deadline).total_seconds() // 60))

    @property
    def is_present(self):
        return self.departed_at is None

    @property
    def duration(self):
        return self.departed_at - self.arrived_at if self.departed_at else None

    def __str__(self):
        return f"{self.employee} — {self.work_date}"


class StaffExitPass(models.Model):
    PERSONAL = "PERSONAL"
    OFFICIAL = "OFFICIAL"
    PASS_TYPES = ((PERSONAL, "Personal errand"), (OFFICIAL, "Official hotel errand"))
    AUTHORISED = "AUTHORISED"
    OUTSIDE = "OUTSIDE"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = ((AUTHORISED, "Authorised — awaiting departure"), (OUTSIDE, "Employee outside"), (RETURNED, "Returned"), (CANCELLED, "Cancelled"))

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="staff_exit_passes")
    pass_number = models.CharField(max_length=30, unique=True, blank=True)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_exit_passes")
    employee_name = models.CharField(max_length=160)
    department_name = models.CharField(max_length=120, blank=True)
    pass_type = models.CharField(max_length=12, choices=PASS_TYPES)
    destination = models.CharField(max_length=200)
    purpose = models.TextField()
    expected_return_at = models.DateTimeField()
    authorised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authorised_staff_exit_passes")
    authorised_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=AUTHORISED)
    gate_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_exit_gate_outs")
    gate_out_at = models.DateTimeField(null=True, blank=True)
    gate_out_note = models.TextField(blank=True)
    gate_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_exit_gate_ins")
    gate_in_at = models.DateTimeField(null=True, blank=True)
    gate_in_note = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="cancelled_staff_exit_passes")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["hotel", "status"]), models.Index(fields=["hotel", "expected_return_at"]), models.Index(fields=["hotel", "employee", "created_at"])]

    def clean(self):
        errors = {}
        if self.employee_id:
            employee_hotel = self.employee.hotel_id or (self.employee.department.hotel_id if self.employee.department_id else None)
            if not self.employee.is_active or employee_hotel != self.hotel_id:
                errors["employee"] = "Select an active employee assigned to this hotel."
            if self.employee.role in {"ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT"}:
                errors["employee"] = "This account is not eligible for staff movement passes."
        if self.expected_return_at and self.expected_return_at <= self.authorised_at:
            errors["expected_return_at"] = "Expected return must be after authorisation."
        if errors:
            raise ValidationError(errors)

    @property
    def is_overdue(self):
        return self.status == self.OUTSIDE and self.expected_return_at < timezone.now()

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating and not self.pass_number:
            self.pass_number = f"TMP-{uuid.uuid4().hex[:20]}"
        if self.employee_id:
            self.employee_name = self.employee.get_full_name() or self.employee.username
            self.department_name = self.employee.department.name if self.employee.department_id else ""
        super().save(*args, **kwargs)
        if creating and self.pass_number.startswith("TMP-"):
            self.pass_number = f"SP-{self.hotel_id}-{self.pk:07d}"
            super().save(update_fields=["pass_number"])

    def __str__(self):
        return f"{self.pass_number or 'New staff pass'} — {self.employee_name}"


class SecurityIncident(models.Model):
    CATEGORY_CHOICES = [
        ("THEFT", "Theft / suspected theft"),
        ("LOSS", "Lost property"),
        ("DAMAGE", "Damage to hotel property"),
        ("SAFETY", "Safety hazard / accident"),
        ("SECURITY_BREACH", "Security breach / unauthorised access"),
        ("DISTURBANCE", "Disorder / disturbance"),
        ("FIRE", "Fire / emergency"),
        ("MEDICAL", "Medical emergency"),
        ("MISCONDUCT", "Staff / guest misconduct"),
        ("SUSPICIOUS", "Suspicious observation"),
        ("OTHER", "Other"),
    ]
    SEVERITY_CHOICES = [
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("CRITICAL", "Critical"),
    ]
    STATUS_CHOICES = [
        ("SUBMITTED", "Awaiting Security Head review"),
        ("UNDER_REVIEW", "Under review"),
        ("INVESTIGATION", "Under investigation"),
        ("ESCALATED", "Escalated"),
        ("RESOLVED", "Resolved"),
        ("CLOSED", "Closed"),
    ]

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="security_incidents")
    incident_number = models.CharField(max_length=32, unique=True, blank=True)
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES)
    severity = models.CharField(max_length=12, choices=SEVERITY_CHOICES, default="MEDIUM")
    occurred_at = models.DateTimeField()
    location = models.CharField(max_length=200)
    summary = models.CharField(max_length=250)
    description = models.TextField()
    persons_involved = models.TextField(blank=True)
    witnesses = models.TextField(blank=True)
    immediate_action = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_incidents_reported")
    reported_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="SUBMITTED")
    assigned_investigator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_incidents_assigned")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_incidents_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution_summary = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_incidents_resolved")
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_incidents_closed")
    closed_at = models.DateTimeField(null=True, blank=True)
    evidence = models.FileField(upload_to="security/incidents/%Y/%m/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(fields=["hotel", "status", "occurred_at"]),
            models.Index(fields=["hotel", "severity", "occurred_at"]),
        ]

    def clean(self):
        errors = {}
        if self.reported_by_id:
            officer = self.reported_by
            if (
                officer.role != "SECURITY" or not officer.is_active
                or not officer.department_id
                or officer.department.department_type != "SECURITY"
                or officer.department.hotel_id != self.hotel_id
            ):
                errors["reported_by"] = "The incident must be recorded by an active Security officer assigned to this hotel."
        if self.assigned_investigator_id:
            officer = self.assigned_investigator
            if (
                officer.role != "SECURITY" or not officer.is_active
                or not officer.department_id
                or officer.department.department_type != "SECURITY"
                or officer.department.hotel_id != self.hotel_id
            ):
                errors["assigned_investigator"] = "Choose an active Security officer from this hotel's Security department."
        if self.reviewed_by_id and not self.reviewed_by.is_security_head:
            errors["reviewed_by"] = "Only the Security Head can review an incident."
        if self.resolved_by_id and not self.resolved_by.is_security_head:
            errors["resolved_by"] = "Only the Security Head can resolve an incident."
        if self.closed_by_id and not self.closed_by.is_security_head:
            errors["closed_by"] = "Only the Security Head can close an incident."
        if self.status in {"RESOLVED", "CLOSED"} and not self.resolution_summary.strip():
            errors["resolution_summary"] = "Record the resolution or closure summary."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating and not self.incident_number:
            self.incident_number = f"TEMP-{uuid.uuid4().hex[:20]}"
        super().save(*args, **kwargs)
        if creating and self.incident_number.startswith("TEMP-"):
            self.incident_number = f"SI-{self.hotel_id}-{self.pk:07d}"
            super().save(update_fields=["incident_number"])

    def __str__(self):
        return f"{self.incident_number or 'New incident'} — {self.summary}"


class SecurityIncidentEvent(models.Model):
    EVENT_CHOICES = [
        ("SUBMITTED", "Incident submitted"),
        ("REVIEWED", "Security Head review"),
        ("INVESTIGATION", "Investigation update"),
        ("ESCALATED", "Escalated"),
        ("RESOLVED", "Resolved"),
        ("CLOSED", "Closed"),
        ("NOTE", "Case note"),
    ]
    incident = models.ForeignKey(SecurityIncident, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=16, choices=EVENT_CHOICES)
    note = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_incident_events")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.incident.incident_number} · {self.get_event_type_display()}"


class SecurityOccurrenceReport(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="security_occurrence_reports")
    duty_date = models.DateField(default=timezone.localdate)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_occurrence_reports_submitted")
    submitted_at = models.DateTimeField(default=timezone.now)
    shift_summary = models.TextField()
    outstanding_matters = models.TextField(blank=True)
    activity_snapshot = models.JSONField(default=dict, blank=True)
    incidents = models.ManyToManyField(SecurityIncident, blank=True, related_name="occurrence_reports")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_occurrence_reports_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-duty_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "duty_date"], name="uniq_security_occurrence_hotel_day"),
        ]
        indexes = [models.Index(fields=["hotel", "duty_date"])]

    def clean(self):
        errors = {}
        if self.submitted_by_id and (
            self.submitted_by.role != "SECURITY" or not self.submitted_by.is_active
            or not self.submitted_by.department_id
            or self.submitted_by.department.department_type != "SECURITY"
            or self.submitted_by.department.hotel_id != self.hotel_id
        ):
            errors["submitted_by"] = "The occurrence report must be submitted by an active Security officer at this hotel."
        if self.reviewed_by_id and not self.reviewed_by.is_security_head:
            errors["reviewed_by"] = "Only the Security Head can review the occurrence report."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.hotel} · Daily occurrence · {self.duty_date}"
