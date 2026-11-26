import calendar
import uuid
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from inventory.models import Department, Hotel


class GymMembershipPlan(models.Model):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"
    PACKAGE_CHOICES = (
        (DAILY, "Daily"),
        (WEEKLY, "Weekly"),
        (MONTHLY, "Monthly"),
        (QUARTERLY, "Quarterly"),
        (ANNUAL, "Annual"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="gym_membership_plans")
    package = models.CharField(max_length=12, choices=PACKAGE_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "package"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "package"], name="uniq_gym_plan_hotel_package"),
            models.CheckConstraint(condition=Q(price__gte=Decimal("0.00")), name="gym_plan_price_nonnegative"),
        ]

    def clean(self):
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": "Membership price cannot be negative."})

    def __str__(self):
        return f"{self.get_package_display()} · {self.hotel.name}"


class GymMember(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="gym_members")
    member_number = models.CharField(max_length=30, blank=True)
    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    gender = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True)
    photograph = models.ImageField(upload_to="gym/members/%Y/%m/", blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gym_members_registered")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "member_number"], name="uniq_gym_member_number_hotel"),
        ]
        indexes = [models.Index(fields=["hotel", "last_name", "first_name"], name="gym_member_hotel_name_idx")]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        if not self.member_number:
            # Generate the identifier before the first INSERT so the hotel's
            # unique constraint never sees multiple temporary blank values.
            self.member_number = f"GYM-{self.hotel_id:03d}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member_number or 'New member'} · {self.full_name}"


class GymMembership(models.Model):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"
    STATUS_CHOICES = (
        (ACTIVE, "Active"),
        (EXPIRED, "Expired"),
        (CANCELLED, "Cancelled"),
        (SUSPENDED, "Suspended"),
    )

    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="gym_memberships")
    member = models.ForeignKey(GymMember, on_delete=models.PROTECT, related_name="memberships")
    plan = models.ForeignKey(GymMembershipPlan, on_delete=models.PROTECT, related_name="memberships")
    plan_name_snapshot = models.CharField(max_length=30)
    amount_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ACTIVE)
    pass_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    billing_payment = models.OneToOneField(
        "billing.Payment", on_delete=models.PROTECT, null=True, blank=True,
        related_name="gym_membership",
    )
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gym_memberships_registered")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["hotel", "status", "end_date"], name="gym_membership_expiry_idx"),
            models.Index(fields=["member", "start_date"], name="gym_member_history_idx"),
        ]

    @staticmethod
    def calculate_end_date(start_date, package):
        if package == GymMembershipPlan.DAILY:
            return start_date
        if package == GymMembershipPlan.WEEKLY:
            from datetime import timedelta
            return start_date + timedelta(days=6)
        months = {GymMembershipPlan.MONTHLY: 1, GymMembershipPlan.QUARTERLY: 3, GymMembershipPlan.ANNUAL: 12}.get(package)
        if months is None:
            raise ValidationError("Select a valid Gym membership package.")
        month_index = start_date.month - 1 + months
        year = start_date.year + month_index // 12
        month = month_index % 12 + 1
        day = min(start_date.day, calendar.monthrange(year, month)[1])
        from datetime import timedelta
        return date(year, month, day) - timedelta(days=1)

    def clean(self):
        errors = {}
        if self.member_id and self.hotel_id and self.member.hotel_id != self.hotel_id:
            errors["member"] = "Member and membership must belong to the same hotel."
        if self.plan_id and self.hotel_id and self.plan.hotel_id != self.hotel_id:
            errors["plan"] = "Membership plan must belong to the same hotel."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "Membership end date cannot be before its start date."
        if errors:
            raise ValidationError(errors)

    @property
    def is_currently_valid(self):
        today = timezone.localdate()
        return self.status == self.ACTIVE and self.start_date <= today <= self.end_date

    def __str__(self):
        return f"{self.member.member_number} · {self.plan_name_snapshot}"


class GymVisit(models.Model):
    membership = models.ForeignKey(GymMembership, on_delete=models.PROTECT, related_name="visits")
    hotel = models.ForeignKey(Hotel, on_delete=models.PROTECT, related_name="gym_visits")
    entered_at = models.DateTimeField(default=timezone.now)
    exited_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gym_visits_checked_in")
    checked_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="gym_visits_checked_out")
    gate_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-entered_at", "-pk"]
        indexes = [models.Index(fields=["hotel", "entered_at"], name="gym_visit_hotel_date_idx")]
        constraints = [
            models.UniqueConstraint(fields=["membership"], condition=Q(exited_at__isnull=True), name="uniq_open_gym_visit_membership"),
        ]

    def clean(self):
        if self.membership_id and self.hotel_id and self.membership.hotel_id != self.hotel_id:
            raise ValidationError({"membership": "Visit and membership must belong to the same hotel."})
        if self.exited_at and self.exited_at < self.entered_at:
            raise ValidationError({"exited_at": "Exit time cannot be before entry time."})

    def __str__(self):
        return f"{self.membership.member.member_number} · {self.entered_at:%d %b %Y %H:%M}"
