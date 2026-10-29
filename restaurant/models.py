from django.db import models, transaction
from django.conf import settings
from inventory.models import Product, Department, stock_out
from django.utils import timezone
from inventory.models import Stock,StockMovement
from kitchen.models import KitchenTicket, KitchenTicketItem
from billing.models import Charge, Payment, Folio
from rooms.models import Room
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Q
from accounting.models import BusinessDay



# from inventory.services.stock import stock_in  # adjust import if needed


User = settings.AUTH_USER_MODEL


# =========================
# MENU ITEM (WHAT IS SOLD)
# =========================
class MenuItem(models.Model):
    CATEGORY_CHOICES = (
        ('DRINK', 'Drink'),
        ('FOOD', 'Food'),
    )

    name = models.CharField(max_length=150)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(
        max_length=10,
        choices=CATEGORY_CHOICES,
        default='DRINK'
    )
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.product_id:
            if self.product.product_type == "FOOD":
                self.category = "FOOD"
            elif self.product.product_type == "DRINK":
                self.category = "DRINK"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.category})"
    
    def get_cost_price(self):
    # If product has a recipe → calculate from ingredients
        if hasattr(self.product, "recipe"):
            return self.product.recipe.total_cost()

        # Otherwise fallback (e.g. drinks)
        return self.product.cost_price


    def get_profit(self):
        return self.price - self.get_cost_price()
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                name="unique_menu_item_per_product"
            )
        ]


User = settings.AUTH_USER_MODEL




class POSOrder(models.Model):

    STATUS_CHOICES = (
        ('OPEN', 'Open'),
        ('CHARGED', 'Charged'),
        ('PAID', 'Paid'),
        ('CANCELLED', 'Cancelled'),
    )

    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    folio = models.ForeignKey(
        Folio,
        on_delete=models.PROTECT,
        related_name="pos_orders"
    )

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='OPEN'
    )
    shift = models.ForeignKey(
        "restaurant.Shift",
        on_delete=models.PROTECT,
        related_name="orders",
        null=True,
        blank=True
    )

    table = models.ForeignKey(
        "restaurant.RestaurantTable",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders"
    )

    business_day = models.ForeignKey(
        BusinessDay,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    is_posted = models.BooleanField(default=False)
    is_cogs_posted = models.BooleanField(default=False)
    # Refund tracking
    is_refunded = models.BooleanField(default=False)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunded_orders"
    )
    refund_reason = models.TextField(blank=True)

    def __str__(self):
        return f"POS-{self.id} ({self.status})"

    # -----------------------------------------
    # 🔒 Internal Safety Check (Hotel Isolation)
    # -----------------------------------------
    def _validate_hotel_integrity(self):

        if not self.department:
            raise ValidationError("Order must belong to a department.")

        hotel = self.department.hotel

        if not hotel:
            raise ValidationError("Department must belong to a hotel.")

        if self.created_by and self.created_by.department:
            if self.created_by.department.hotel != hotel:
                raise ValidationError("User and order must belong to same hotel.")

        if self.folio and hasattr(self.folio, "hotel"):
            if self.folio.hotel != hotel:
                raise ValidationError("Folio and order must belong to same hotel.")

        return hotel

    # =====================================
    # CHARGE ORDER
    # =====================================
    @transaction.atomic
    def charge_order(self):

        if self.status != "OPEN":
            raise ValidationError("Only OPEN orders can be charged.")

        hotel = self._validate_hotel_integrity()

        total = Decimal("0.00")

        restaurant = self.department

        # Resolve the Kitchen for prepared FOOD.
        kitchen = (
            Department.objects
            .filter(
                hotel=restaurant.hotel,
                department_type="KITCHEN",
                is_active=True,
            )
            .first()
        )

        if not kitchen:
            raise ValidationError("Kitchen department not found.")

        # =========================
        # PROCESS ORDER ITEMS
        # =========================
        for item in self.items.select_related("menu_item__product"):

            product = item.menu_item.product
            if product.hotel_id != hotel.pk:
                raise ValidationError(
                    "Every POS menu item must belong to the same hotel as the order."
                )
            qty = item.quantity

            line_total = qty * item.price
            total += line_total

            if not product.is_stock_item():
                continue

            # Drinks come from Restaurant stock.
            # Prepared food comes from Kitchen stock.
            if product.product_type == "DRINK":
                stock_department = restaurant

            elif product.product_type == "FOOD":
                stock_department = kitchen

            else:
                continue

            result = stock_out(
                product=product,
                department=stock_department,
                quantity=qty,
                user=self.created_by,
                reason="Restaurant POS sale",
                reference=f"POS-{self.id}",
            )

            # The FIFO engine is now the source of the historical
            # cost used by COGS.
            item.cost_at_sale = result["total_cost"] / Decimal(qty)
            item.save(update_fields=["cost_at_sale"])

        # =========================
        # CREATE KITCHEN TICKET
        # =========================
        food_items = [
            item
            for item in self.items.select_related("menu_item__product")
            if item.menu_item.product.product_type == "FOOD"
        ]

        if food_items:

            ticket = KitchenTicket.objects.create(
                order=self,
                room=(
                    self.folio.room
                    if self.folio.folio_type == "ROOM"
                    else None
                ),
            )

            for item in food_items:
                KitchenTicketItem.objects.create(
                    ticket=ticket,
                    menu_item=item.menu_item,
                    quantity=item.quantity,
                )

        # =========================
        # CREATE FOLIO CHARGE
        # =========================
        Charge.objects.create(
            folio=self.folio,
            description=f"Restaurant Order #{self.id}",
            department=self.department,
            amount=total,
            reference=f"POS-{self.id}",
        )

        # =========================
        # FINALIZE ORDER
        # =========================
        self.total_amount = total
        self.status = "CHARGED"
        self.save(
            update_fields=[
                "total_amount",
                "status",
            ]
        )
        
    # =====================================
    # PAY ORDER
    # =====================================
    @transaction.atomic
    def pay_order(self, method="CASH", account=None):

        if self.status != "CHARGED":
            raise ValidationError("Only CHARGED orders can be paid.")

        self._validate_hotel_integrity()

        Payment.objects.create(
            folio=self.folio,
            amount=self.total_amount,
            method=method,
            account=account,
            shift=self.shift,
            collected_by=self.created_by,
            reference=f"POS-{self.id}"
        )

        self.status = "PAID"
        self.save(update_fields=["status"])

    @transaction.atomic
    def refund(order, user, reason=""):

        from restaurant.workflows.refund_order import refund_order

        return refund_order(
            order=order,
            user=user,
            reason=reason,
        )
# =========================
# POS ORDER ITEM (LINE ITEM)
# =========================
class POSOrderItem(models.Model):
    order = models.ForeignKey(
        POSOrder,
        related_name='items',
        on_delete=models.CASCADE
    )
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # Preserve fractional unit costs from FIFO allocations. Currency totals
    # are rounded by the accounting layer, but unit cost must retain enough
    # precision for small quantities and recipe-produced food.
    cost_at_sale = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=0
    )

    def __str__(self):
        return f"{self.menu_item} x {self.quantity}"

    def line_total(self):
        return self.quantity * self.price


class Shift(models.Model):

    STATUS_CHOICES = (
        ("OPEN", "Open"),
        ("CLOSED", "Closed"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="restaurant_shifts"
    )

    department = models.ForeignKey(
        "inventory.Department",
        on_delete=models.PROTECT
    )

    opened_at = models.DateTimeField(auto_now_add=True)

    closed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    opening_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    closing_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="OPEN"
    )

    notes = models.TextField(blank=True)

class Meta:
    ordering = ["-opened_at"]


def close(self, closing_cash):

    self.closing_cash = closing_cash
    self.closed_at = timezone.now()
    self.status = "CLOSED"

    self.save()


def __str__(self):
    return f"{self.user} - {self.opened_at}"


def save(self, *args, **kwargs):
    if (
        not self.pk
        and self.status == "OPEN"
        and self.department_id
        and self.department.department_type == "RESTAURANT"
    ):
        existing = (
            Shift.objects
            .filter(
                department=self.department,
                status="OPEN",
            )
            .exclude(pk=self.pk)
            .first()
        )

        if existing:
            raise ValueError(
                "This Restaurant department already has an open shift."
            )

    super().save(*args, **kwargs)

class RestaurantTable(models.Model):

    department = models.ForeignKey(
        "inventory.Department",
        on_delete=models.PROTECT
    )

    name = models.CharField(max_length=50)

    capacity = models.PositiveIntegerField(default=4)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name