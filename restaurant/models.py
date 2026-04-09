from django.db import models, transaction
from django.conf import settings
from inventory.models import Product, Department, stock_out, stock_in
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

        kitchen = Department.objects.filter(
            hotel=restaurant.hotel,
            department_type="KITCHEN",
            is_active=True
        ).first()

        # =========================
        # PROCESS ORDER ITEMS
        # =========================
        for item in self.items.select_related("menu_item__product"):

            product = item.menu_item.product
            qty = item.quantity

            line_total = qty * item.price
            total += line_total

            # =========================
            # DRINK STOCK (POS HANDLES)
            # =========================
            if product.product_type == "DRINK":

                stock = Stock.objects.select_for_update().filter(
                    product=product,
                    department=restaurant
                ).first()

                if not stock or stock.quantity < qty:
                    raise ValidationError(
                        f"Insufficient restaurant stock for {product.name}"
                    )

                stock.quantity -= qty
                stock.save(update_fields=["quantity"])

                StockMovement.objects.create(
                    product=product,
                    from_department=restaurant,
                    quantity=qty,
                    movement_type="OUT",
                    created_by=self.created_by,
                    reference=f"POS-{self.id}"
                )

        # =========================
        # CREATE KITCHEN TICKET (FOOD ONLY)
        # =========================
        food_items = [
            item for item in self.items.select_related("menu_item__product")
            if item.menu_item.product.product_type == "FOOD"
        ]

        if food_items:

            ticket = KitchenTicket.objects.create(
                order=self,
                room=self.folio.room if self.folio.folio_type == "ROOM" else None
            )

            for item in food_items:
                KitchenTicketItem.objects.create(
                    ticket=ticket,
                    menu_item=item.menu_item,
                    quantity=item.quantity
                )

        # =========================
        # CREATE FOLIO CHARGE
        # =========================
        Charge.objects.create(
            folio=self.folio,
            description=f"Restaurant Order #{self.id}",
            department=self.department,
            amount=total,
            reference=f"POS-{self.id}"
        )

        # =========================
        # FINALIZE ORDER
        # =========================
        self.total_amount = total
        self.status = "CHARGED"
        self.save(update_fields=["total_amount", "status"])

    # =====================================
    # PAY ORDER
    # =====================================
    @transaction.atomic
    def pay_order(self, method="CASH"):

        if self.status != "CHARGED":
            raise ValidationError("Only CHARGED orders can be paid.")

        self._validate_hotel_integrity()

        Payment.objects.create(
            folio=self.folio,
            amount=self.total_amount,
            method=method,
            collected_by=self.created_by,
            reference=f"POS-{self.id}"
        )

        self.status = "PAID"
        self.save(update_fields=["status"])

    # =====================================
    # REFUND ORDER
    # =====================================
    @transaction.atomic
    def refund(self, user, reason=""):

        if self.is_refunded:
            raise ValidationError("Order already refunded.")

        if self.status not in ["CHARGED", "PAID"]:
            raise ValidationError("Only charged or paid orders can be refunded.")

        if self.status == "CANCELLED":
            raise ValidationError("Cancelled orders cannot be refunded.")

        if not user:
            raise ValidationError("Refund user required.")

        hotel = self._validate_hotel_integrity()

        # 1️⃣ Restore stock to restaurant department
        for item in self.items.select_related("menu_item__product"):

            stock = Stock.objects.select_for_update().filter(
                product=item.menu_item.product,
                department=self.department
            ).first()

            if not stock:
                stock = Stock.objects.create(
                    product=item.menu_item.product,
                    department=self.department,
                    quantity=0
                )

            stock.quantity += item.quantity
            stock.save(update_fields=["quantity"])

            StockMovement.objects.create(
                product=item.menu_item.product,
                to_department=self.department,
                quantity=item.quantity,
                movement_type="IN",
                created_by=user,
                reference=f"POS-REFUND-{self.id}"
            )

        # 2️⃣ Reverse financials
        if self.status == "PAID":
            Payment.objects.create(
                folio=self.folio,
                amount=-self.total_amount,
                method="REFUND",
                collected_by=user,
                reference=f"POS-REFUND-{self.id}"
            )

        Charge.objects.create(
            folio=self.folio,
            description=f"Refund for POS Order #{self.id}",
            department=self.department,
            amount=-self.total_amount,
            reference=f"POS-REFUND-{self.id}"
        )

        self.is_refunded = True
        self.refunded_at = timezone.now()
        self.refunded_by = user
        self.refund_reason = reason
        self.status = "CANCELLED"

        self.save(update_fields=[
            "is_refunded",
            "refunded_at",
            "refunded_by",
            "refund_reason",
            "status"
        ])


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
        constraints = [
            models.UniqueConstraint(
                fields=["user", "department"],
                condition=Q(status="OPEN"),
                name="one_open_shift_per_user"
            )
        ]

    def close(self, closing_cash):

        self.closing_cash = closing_cash
        self.closed_at = timezone.now()
        self.status = "CLOSED"

        self.save()

    def __str__(self):
        return f"{self.user} - {self.opened_at}"
    
    def save(self, *args, **kwargs):

        if not self.pk and self.status == "OPEN":

            existing = Shift.objects.filter(
                user=self.user,
                status="OPEN"
            ).exists()

            if existing:
                raise ValueError("User already has an open shift.")

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