from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError
from inventory.models import Department


class User(AbstractUser):
    ROLE_CHOICES = (
        ('ADMIN', 'Admin'),
        ('MANAGER', 'Manager'),

        # Front of house
        ('FRONTDESK', 'Front Desk / Reception'),

        # Operations
        ('RESTAURANT', 'Restaurant Staff'),
        ('STORE', 'Store Manager'),
        ('KITCHEN', 'Kitchen Staff'),
        ('HOUSEKEEPING', 'Housekeeping'),
        ('LAUNDRY', 'Laundry Staff'),
        ('GYM', 'Gym Staff'),

        # Finance
        ('ACCOUNTANT', 'Accountant'),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='FRONTDESK'
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Required for operational staff (restaurant, store, kitchen, housekeeping, etc.)"
    )

    phone = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.username} ({self.role})"

    # =========================
    # VALIDATION RULES
    # =========================
    def clean(self):
        """
        Enforce role → department consistency
        """
        department_required_roles = {
            'RESTAURANT',
            'STORE',
            'KITCHEN',
            'HOUSEKEEPING',
            'LAUNDRY',
            'GYM',
        }

        if self.role in department_required_roles and not self.department:
            raise ValidationError({
                'department': f"{self.role} users must be assigned a department."
            })

        if self.role == 'ADMIN' and self.department:
            raise ValidationError({
                'department': "Admin users should not be tied to a department."
            })

    # =========================
    # ROLE HELPERS (CLEAN USAGE)
    # =========================
    @property
    def is_admin(self):
        return self.role == 'ADMIN'

    @property
    def is_frontdesk(self):
        return self.role == 'FRONTDESK'

    @property
    def is_restaurant(self):
        return self.role == 'RESTAURANT'

    @property
    def is_store(self):
        return self.role == 'STORE'

    @property
    def is_kitchen(self):
        return self.role == 'KITCHEN'

    @property
    def is_housekeeping(self):
        return self.role == 'HOUSEKEEPING'
