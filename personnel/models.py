from datetime import datetime, time, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from .storage import private_employee_storage


class ShiftDefinition(models.Model):
    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.CASCADE, related_name="personnel_shifts")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=80)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time", "name"]
        constraints = [models.UniqueConstraint(fields=["hotel", "code"], name="uniq_personnel_shift_code_hotel")]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    @staticmethod
    def _as_time(value):
        # Newly-created model instances may still hold the string supplied to
        # objects.create(); database-loaded instances return datetime.time.
        return time.fromisoformat(value) if isinstance(value, str) else value

    @property
    def crosses_midnight(self):
        return self._as_time(self.end_time) <= self._as_time(self.start_time)

    def interval_for(self, work_date):
        start_time = self._as_time(self.start_time)
        end_time = self._as_time(self.end_time)
        start = timezone.make_aware(datetime.combine(work_date, start_time))
        end_date = work_date + timedelta(days=1) if end_time <= start_time else work_date
        end = timezone.make_aware(datetime.combine(end_date, end_time))
        return start, end

    def clean(self):
        if self.hotel_id is None:
            raise ValidationError({"hotel": "A shift must belong to a hotel."})


class WeeklyRoster(models.Model):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"), (SUBMITTED, "Submitted for review"),
        (APPROVED, "Approved"), (RETURNED, "Returned for changes"),
    ]

    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.PROTECT, related_name="weekly_rosters")
    department = models.ForeignKey("inventory.Department", on_delete=models.PROTECT, related_name="weekly_rosters")
    week_start = models.DateField(help_text="Monday of the roster week")
    responsible_hod = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="responsible_rosters")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_rosters")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_rosters")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_rosters")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start", "department__name"]
        constraints = [models.UniqueConstraint(fields=["hotel", "department", "week_start"], name="uniq_weekly_roster_department_week")]

    def __str__(self):
        return f"{self.department.name} · week of {self.week_start}"

    @property
    def week_end(self):
        return self.week_start + timedelta(days=6)

    def clean(self):
        errors = {}
        if self.week_start and self.week_start.weekday() != 0:
            errors["week_start"] = "Roster week must start on a Monday."
        if self.department_id and self.hotel_id and self.department.hotel_id != self.hotel_id:
            errors["department"] = "Department must belong to the selected hotel."
        if self.responsible_hod_id and self.department_id:
            hod = self.responsible_hod
            if not hod.is_active or hod.department_id != self.department_id or not hod.is_department_head:
                errors["responsible_hod"] = "Select an active Head of the roster department."
        if errors:
            raise ValidationError(errors)


class RosterAssignment(models.Model):
    roster = models.ForeignKey(WeeklyRoster, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="roster_assignments")
    work_date = models.DateField()
    shift = models.ForeignKey(ShiftDefinition, on_delete=models.PROTECT, null=True, blank=True, related_name="assignments")
    is_day_off = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["work_date", "employee__first_name", "employee__username"]
        constraints = [models.UniqueConstraint(fields=["roster", "employee", "work_date"], name="uniq_roster_employee_workday")]

    def clean(self):
        errors = {}
        if self.roster_id and self.work_date:
            if not self.roster.week_start <= self.work_date <= self.roster.week_end:
                errors["work_date"] = "Assignment date must fall within the roster week."
        if self.roster_id and self.employee_id:
            employee_hotel_id = self.employee.department.hotel_id if self.employee.department_id else None
            if self.employee.department_id != self.roster.department_id or employee_hotel_id != self.roster.hotel_id:
                errors["employee"] = "Employee must be active in this roster's department and hotel."
            if not self.employee.is_active:
                errors["employee"] = "Inactive employees cannot be rostered."
        if self.is_day_off and self.shift_id:
            errors["shift"] = "A day-off entry cannot also have a shift."
        if not self.is_day_off and not self.shift_id:
            errors["shift"] = "Choose a shift or mark the employee as off."
        if self.shift_id and self.roster_id and self.shift.hotel_id != self.roster.hotel_id:
            errors["shift"] = "Shift must belong to the roster hotel."
        if errors:
            raise ValidationError(errors)


class RosterAuditEvent(models.Model):
    roster = models.ForeignKey(WeeklyRoster, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="roster_audit_events")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.roster} · {self.event_type}"


class RosterReconciliation(models.Model):
    """HR disposition of an arrival recorded on an employee's approved day off."""
    PENDING = "PENDING"
    RECONCILED = "RECONCILED"
    STATUS_CHOICES = [(PENDING, "Pending HR review"), (RECONCILED, "Reconciled")]
    SHIFT_SWAP = "SHIFT_SWAP"
    EMERGENCY_COVER = "EMERGENCY_COVER"
    CALLED_IN = "CALLED_IN"
    OVERTIME = "OVERTIME"
    OTHER_WORK = "OTHER_WORK"
    NO_WORK = "NO_WORK"
    REASON_CHOICES = [
        (SHIFT_SWAP, "Shift swap"), (EMERGENCY_COVER, "Emergency cover"),
        (CALLED_IN, "Called in by management"), (OVERTIME, "Additional / overtime work"),
        (OTHER_WORK, "Other work arrangement"), (NO_WORK, "Visit only — no work performed"),
    ]
    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.PROTECT, related_name="roster_reconciliations")
    attendance = models.OneToOneField("security.StaffAttendance", on_delete=models.PROTECT, related_name="roster_reconciliation")
    roster_assignment = models.ForeignKey(RosterAssignment, on_delete=models.PROTECT, related_name="reconciliations")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="roster_reconciliations")
    reason = models.CharField(max_length=24, choices=REASON_CHOICES, blank=True)
    covered_employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="covered_by_reconciliations")
    actual_shift_name = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="completed_roster_reconciliations")
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-attendance__arrived_at", "-pk"]
        indexes = [models.Index(fields=["hotel", "status"])]

    def clean(self):
        errors = {}
        if self.attendance_id:
            if self.attendance.hotel_id != self.hotel_id:
                errors["attendance"] = "Attendance must belong to the same hotel."
            if self.attendance.employee_id != self.employee_id:
                errors["employee"] = "Reconciliation employee must match the attendance record."
        if self.roster_assignment_id and self.attendance_id:
            assignment = self.roster_assignment
            if (assignment.employee_id != self.employee_id or assignment.work_date != self.attendance.work_date
                or assignment.roster.hotel_id != self.hotel_id or not assignment.is_day_off
                or assignment.roster.status != WeeklyRoster.APPROVED):
                errors["roster_assignment"] = "The linked assignment must be this employee's approved day off."
        if self.covered_employee_id and self.employee.department_id != self.covered_employee.department_id:
            errors["covered_employee"] = "The covered employee must belong to the same department."
        if self.status == self.RECONCILED and (not self.reconciled_by_id or not self.reconciled_at):
            errors["status"] = "A completed reconciliation must record who signed it off and when."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.employee} · {self.attendance.work_date} · {self.get_status_display()}"


class EmployeeProfile(models.Model):
    """Authoritative HR record; a login account is optional."""

    PERMANENT = "PERMANENT"
    CONTRACT = "CONTRACT"
    TEMPORARY = "TEMPORARY"
    CASUAL = "CASUAL"
    INTERN = "INTERN"
    EMPLOYMENT_TYPE_CHOICES = [
        (PERMANENT, "Permanent"),
        (CONTRACT, "Contract"),
        (TEMPORARY, "Temporary"),
        (CASUAL, "Casual"),
        (INTERN, "Intern"),
    ]

    ACTIVE = "ACTIVE"
    ON_LEAVE = "ON_LEAVE"
    SUSPENDED = "SUSPENDED"
    RESIGNED = "RESIGNED"
    RETIRED = "RETIRED"
    TERMINATED = "TERMINATED"
    EMPLOYMENT_STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (ON_LEAVE, "On leave"),
        (SUSPENDED, "Suspended"),
        (RESIGNED, "Resigned"),
        (RETIRED, "Retired"),
        (TERMINATED, "Terminated"),
    ]

    employee_number = models.CharField(max_length=32, unique=True, editable=False)
    hotel = models.ForeignKey(
        "inventory.Hotel", on_delete=models.PROTECT,
        related_name="employee_profiles",
    )
    department = models.ForeignKey(
        "inventory.Department", on_delete=models.PROTECT,
        related_name="employee_profiles",
    )
    linked_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="employee_profile",
        help_text="Optional HMS login account. Employees do not need an account to have an HR record.",
    )

    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    preferred_name = models.CharField(max_length=80, blank=True)
    personal_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    job_title = models.CharField(max_length=120)
    employment_type = models.CharField(
        max_length=12, choices=EMPLOYMENT_TYPE_CHOICES, default=PERMANENT,
    )
    employment_status = models.CharField(
        max_length=12, choices=EMPLOYMENT_STATUS_CHOICES, default=ACTIVE,
    )
    hire_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    hr_notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_employee_profiles",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="updated_employee_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name", "employee_number"]
        indexes = [
            models.Index(fields=["hotel", "employment_status"]),
            models.Index(fields=["hotel", "department"]),
        ]

    def save(self, *args, **kwargs):
        if not self.employee_number:
            import uuid
            hotel_code = str(self.hotel_id or "0").zfill(3)
            self.employee_number = f"EMP-{hotel_code}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        if self.hotel_id and self.department_id:
            if self.department.hotel_id != self.hotel_id:
                errors["department"] = "The department must belong to the selected hotel."
        if self.linked_user_id:
            user_hotel_id = self.linked_user.hotel_id
            if not user_hotel_id and self.linked_user.department_id:
                user_hotel_id = self.linked_user.department.hotel_id
            if user_hotel_id and user_hotel_id != self.hotel_id:
                errors["linked_user"] = "The linked login account must belong to the same hotel."
        if self.end_date and self.hire_date and self.end_date < self.hire_date:
            errors["end_date"] = "The employment end date cannot be before the hire date."
        if errors:
            raise ValidationError(errors)

    @property
    def full_name(self):
        return " ".join(part for part in [self.first_name, self.middle_name, self.last_name] if part)

    def __str__(self):
        return f"{self.employee_number} · {self.full_name}"


class EmployeeEmploymentHistory(models.Model):
    """Append-only audit trail of material employment changes."""

    HIRED = "HIRED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    EVENT_TYPE_CHOICES = [
        (HIRED, "Hired"),
        ("DEPARTMENT_TRANSFER", "Department transfer"),
        ("HOTEL_TRANSFER", "Hotel transfer"),
        ("JOB_TITLE_CHANGE", "Job title / promotion"),
        ("EMPLOYMENT_TYPE_CHANGE", "Employment type change"),
        ("STATUS_CHANGE", "Employment status change"),
        ("END_DATE_CHANGE", "Employment end date change"),
        (PROFILE_UPDATED, "Other employment details updated"),
    ]

    employee = models.ForeignKey(
        EmployeeProfile, on_delete=models.PROTECT,
        related_name="employment_history",
    )
    event_type = models.CharField(max_length=32, choices=EVENT_TYPE_CHOICES)
    effective_date = models.DateField()
    previous_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="recorded_employee_history",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_date", "-created_at", "-pk"]
        indexes = [
            models.Index(fields=["employee", "effective_date"]),
        ]

    def __str__(self):
        return f"{self.employee.employee_number} · {self.get_event_type_display()}"


class EmployeeDocument(models.Model):
    """Private employee document; never expose its storage URL."""

    IDENTITY = "IDENTITY"
    CONTRACT = "CONTRACT"
    QUALIFICATION = "QUALIFICATION"
    APPOINTMENT = "APPOINTMENT"
    PROMOTION = "PROMOTION"
    DISCIPLINARY = "DISCIPLINARY"
    CORRESPONDENCE = "CORRESPONDENCE"
    OTHER = "OTHER"
    CATEGORY_CHOICES = [
        (IDENTITY, "Identity document"),
        (CONTRACT, "Employment contract"),
        (QUALIFICATION, "Qualification / certificate"),
        (APPOINTMENT, "Appointment letter"),
        (PROMOTION, "Promotion / transfer letter"),
        (DISCIPLINARY, "Disciplinary record"),
        (CORRESPONDENCE, "HR correspondence"),
        (OTHER, "Other"),
    ]

    employee = models.ForeignKey(
        EmployeeProfile, on_delete=models.PROTECT,
        related_name="documents",
    )
    title = models.CharField(max_length=160)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.TextField(blank=True)
    file = models.FileField(
        upload_to="personnel/employees/%Y/%m/",
        storage=private_employee_storage,
    )
    original_filename = models.CharField(max_length=255, editable=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="uploaded_employee_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-pk"]

    def save(self, *args, **kwargs):
        if self.file and not self.original_filename:
            self.original_filename = self.file.name.rsplit("/", 1)[-1][:255]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.employee_number} · {self.title}"


class EmployeeDocumentAccess(models.Model):
    document = models.ForeignKey(
        EmployeeDocument, on_delete=models.PROTECT,
        related_name="access_events",
    )
    accessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="employee_document_access_events",
    )
    accessed_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=20, default="DOWNLOAD")

    class Meta:
        ordering = ["-accessed_at", "-pk"]


class PersonnelAction(models.Model):
    """Formal HR action submitted for Director/Admin decision."""

    PROMOTION = "PROMOTION"
    APPOINTMENT = "APPOINTMENT"
    TRANSFER = "TRANSFER"
    QUERY = "QUERY"
    WARNING = "WARNING"
    SUSPENSION = "SUSPENSION"
    REINSTATEMENT = "REINSTATEMENT"
    RESIGNATION = "RESIGNATION"
    RETIREMENT = "RETIREMENT"
    TERMINATION = "TERMINATION"
    DISMISSAL = "DISMISSAL"
    COMMENDATION = "COMMENDATION"
    AWARD = "AWARD"
    ACTION_TYPE_CHOICES = [
        (PROMOTION, "Promotion"), (APPOINTMENT, "Appointment"),
        (TRANSFER, "Transfer"), (QUERY, "Query"),
        (WARNING, "Warning"), (SUSPENSION, "Suspension"),
        (REINSTATEMENT, "Reinstatement"), (RESIGNATION, "Resignation"),
        (RETIREMENT, "Retirement"), (TERMINATION, "Termination"),
        (DISMISSAL, "Dismissal"), (COMMENDATION, "Commendation"),
        (AWARD, "Award"),
    ]

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"), (SUBMITTED, "Pending approval"),
        (APPROVED, "Approved"), (REJECTED, "Rejected"),
        (RETURNED, "Returned for correction"),
    ]

    reference = models.CharField(max_length=32, unique=True, editable=False)
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, related_name="personnel_actions")
    action_type = models.CharField(max_length=20, choices=ACTION_TYPE_CHOICES)
    title = models.CharField(max_length=160)
    reason = models.TextField()
    effective_date = models.DateField()
    proposed_job_title = models.CharField(max_length=120, blank=True)
    proposed_department = models.ForeignKey(
        "inventory.Department", on_delete=models.PROTECT, null=True, blank=True,
        related_name="proposed_personnel_actions",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_personnel_actions",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="decided_personnel_actions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True, editable=False)
    attachment = models.FileField(
        upload_to="personnel/actions/%Y/%m/", storage=private_employee_storage,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["status", "created_at"]), models.Index(fields=["employee", "effective_date"])]

    def save(self, *args, **kwargs):
        if not self.reference:
            import uuid
            self.reference = f"PA-{timezone.now():%Y%m}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def clean(self):
        if self.employee_id and self.proposed_department_id:
            if self.proposed_department.hotel_id != self.employee.hotel_id:
                raise ValidationError({"proposed_department": "The proposed department must belong to the employee's hotel."})

    def __str__(self):
        return f"{self.reference} · {self.employee.full_name}"


class PersonnelActionEvent(models.Model):
    """Append-only audit events for PersonnelAction workflow transitions."""

    action = models.ForeignKey(PersonnelAction, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="personnel_action_events")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.action.reference} · {self.event_type}"


class EmployeeSeparationClearance(models.Model):
    """Department/Personnel verification and management sign-off for separation."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (PENDING, "Pending department / Personnel verification"),
        (SUBMITTED, "Submitted for final approval"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned for further action"),
    ]
    action = models.OneToOneField(PersonnelAction, on_delete=models.PROTECT, related_name="separation_clearance")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    outstanding_items = models.TextField(blank=True)
    no_outstanding_confirmed = models.BooleanField(default=False)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="verified_separation_clearances")
    verified_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_separation_clearances")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_separation_clearances")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at", "-pk"]
        indexes = [models.Index(fields=["status", "created_at"], name="personnel_es_status_created_idx")]

    def clean(self):
        if self.action_id and self.action.action_type not in {
            PersonnelAction.RESIGNATION, PersonnelAction.RETIREMENT,
            PersonnelAction.TERMINATION, PersonnelAction.DISMISSAL,
        }:
            raise ValidationError({"action": "A separation clearance must be linked to a separation Personnel Action."})
        if self.status == self.SUBMITTED and (
            not self.no_outstanding_confirmed or self.outstanding_items.strip()
            or not self.verified_by_id or not self.submitted_by_id
        ):
            raise ValidationError("A submitted clearance must have a completed no-outstanding verification.")
        if self.status == self.APPROVED and (not self.decided_by_id or not self.decided_at):
            raise ValidationError("An approved clearance must record its final approver and decision time.")

    def __str__(self):
        return f"{self.action.reference} · {self.get_status_display()}"


class EmployeeSeparationClearanceEvent(models.Model):
    clearance = models.ForeignKey(EmployeeSeparationClearance, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="separation_clearance_events")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.clearance.action.reference} · {self.event_type}"
