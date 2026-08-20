from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError

from inventory.models import (
    Organization,
    Department,
    Hotel,
)


class User(AbstractUser):

    ROLE_CHOICES = (
        # Platform / organization management
        ("ADMIN", "Admin"),
        ("DIRECTOR", "Director"),
        ("GENERAL_MANAGER", "General Manager"),
        ("MANAGER", "Manager"),

        # Finance
        ("CHIEF_ACCOUNTANT", "Chief Accountant"),
        ("ACCOUNTANT", "Hotel Accountant"),

        # Front of house
        ("FRONTDESK", "Front Desk / Reception"),

        # Operations
        ("RESTAURANT", "Restaurant Staff"),
        ("STORE", "Store Manager"),
        ("KITCHEN", "Kitchen Staff"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("LAUNDRY", "Laundry Staff"),
        ("GYM", "Gym Staff"),
    )

    role = models.CharField(
        max_length=30,
        choices=ROLE_CHOICES,
        default="FRONTDESK",
    )

    # Organization-level scope
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text="Organization this user belongs to.",
    )

    # Single-hotel scope
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text="Hotel assigned to this user when the role is hotel-specific.",
    )

    # Multi-hotel scope
    assigned_hotels = models.ManyToManyField(
        Hotel,
        blank=True,
        related_name="assigned_managers",
        help_text="Hotels assigned to General Managers.",
    )

    # Department scope
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text="Department assigned to operational staff.",
    )

    is_department_head = models.BooleanField(
        default=False,
        help_text="Head of department responsible for assignments and supervision.",
    )

    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )

    def __str__(self):
        return f"{self.username} ({self.role})"

    # =========================================================
    # ROLE HELPERS
    # =========================================================

    @property
    def is_admin(self):
        return self.role == "ADMIN"

    @property
    def is_director(self):
        return self.role == "DIRECTOR"

    @property
    def is_general_manager(self):
        return self.role == "GENERAL_MANAGER"

    @property
    def is_manager(self):
        return self.role == "MANAGER"

    @property
    def is_chief_accountant(self):
        return self.role == "CHIEF_ACCOUNTANT"

    @property
    def is_accountant(self):
        return self.role == "ACCOUNTANT"

    @property
    def is_frontdesk(self):
        return self.role == "FRONTDESK"

    @property
    def is_restaurant(self):
        return self.role == "RESTAURANT"

    @property
    def is_store(self):
        return self.role == "STORE"

    @property
    def is_kitchen(self):
        return self.role == "KITCHEN"

    @property
    def is_housekeeping(self):
        return self.role == "HOUSEKEEPING"

    @property
    def is_laundry(self):
        return self.role == "LAUNDRY"

    @property
    def is_gym(self):
        return self.role == "GYM"

    # =========================================================
    # ROLE GROUPS
    # =========================================================

    @property
    def is_organization_level(self):
        return self.role in {
            "DIRECTOR",
            "GENERAL_MANAGER",
            "CHIEF_ACCOUNTANT",
        }

    @property
    def is_hotel_level(self):
        return self.role in {
            "MANAGER",
            "ACCOUNTANT",
        }

    @property
    def is_operational_staff(self):
        return self.role in {
            "FRONTDESK",
            "RESTAURANT",
            "STORE",
            "KITCHEN",
            "HOUSEKEEPING",
            "LAUNDRY",
            "GYM",
        }

    # =========================================================
    # VALIDATION
    # =========================================================

    def clean(self):

        super().clean()

        # -----------------------------------------------------
        # ADMIN
        # -----------------------------------------------------

        if self.role == "ADMIN":

            self.organization = None
            self.hotel = None
            self.department = None

        # -----------------------------------------------------
        # DIRECTOR
        # -----------------------------------------------------

        elif self.role == "DIRECTOR":

            if not self.organization:
                raise ValidationError(
                    "Director must belong to an organization."
                )

            self.hotel = None
            self.department = None

        # -----------------------------------------------------
        # GENERAL MANAGER
        # -----------------------------------------------------

        elif self.role == "GENERAL_MANAGER":

            if not self.organization:
                raise ValidationError(
                    "General Manager must belong to an organization."
                )

            self.hotel = None
            self.department = None

        # -----------------------------------------------------
        # CHIEF ACCOUNTANT
        # -----------------------------------------------------

        elif self.role == "CHIEF_ACCOUNTANT":

            if not self.organization:
                raise ValidationError(
                    "Chief Accountant must belong to an organization."
                )

            self.hotel = None
            self.department = None

        # -----------------------------------------------------
        # HOTEL MANAGER
        # -----------------------------------------------------

        elif self.role == "MANAGER":

            if not self.hotel:
                raise ValidationError(
                    "Manager must be assigned to a hotel."
                )

            self.organization = self.hotel.organization
            self.department = None

        # -----------------------------------------------------
        # HOTEL ACCOUNTANT
        # -----------------------------------------------------

        elif self.role == "ACCOUNTANT":

            if not self.hotel:
                raise ValidationError(
                    "Hotel Accountant must be assigned to a hotel."
                )

            self.organization = self.hotel.organization
            self.department = None

        # -----------------------------------------------------
        # OPERATIONAL STAFF
        # -----------------------------------------------------

        elif self.role in {
            "FRONTDESK",
            "RESTAURANT",
            "STORE",
            "KITCHEN",
            "HOUSEKEEPING",
            "LAUNDRY",
            "GYM",
        }:

            if not self.department:
                raise ValidationError(
                    "This role requires a department."
                )

            if not self.department.hotel_id:
                raise ValidationError(
                    "The department must belong to a hotel."
                )

            self.hotel = self.department.hotel
            self.organization = self.department.hotel.organization

        # -----------------------------------------------------
        # GENERAL MANAGER HOTEL VALIDATION
        # -----------------------------------------------------

        if self.role == "GENERAL_MANAGER" and self.pk:

            invalid_hotels = self.assigned_hotels.exclude(
                organization=self.organization
            ).exists()

            if invalid_hotels:
                raise ValidationError(
                    "General Manager can only be assigned hotels "
                    "belonging to their organization."
                )

    # =========================================================
    # ACCESS HELPERS
    # =========================================================

    def has_hotel_access(self, hotel):

        if not hotel:
            return False

        if self.role == "ADMIN":
            return True

        if self.role in {
            "DIRECTOR",
            "CHIEF_ACCOUNTANT",
        }:
            return hotel.organization_id == self.organization_id

        if self.role == "GENERAL_MANAGER":
            return self.assigned_hotels.filter(
                pk=hotel.pk
            ).exists()

        return self.hotel_id == hotel.pk