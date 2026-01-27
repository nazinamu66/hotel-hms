from django.db import models, transaction
from django.conf import settings
from inventory.models import Product, Department, stock_out
# from billing.models import Folio, Charge, Payment
from django.utils import timezone
from inventory.models import Stock
from billing.models import Charge, Payment
from django.db import transaction
from rooms.models import Room




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


from django.db import models, transaction
from django.conf import settings

from billing.models import Folio, Charge, Payment
from inventory.models import stock_out
from inventory.models import Department

User = settings.AUTH_USER_MODEL


class POSOrder(models.Model):
    STATUS_CHOICES = (
        ('OPEN', 'Open'),        # being built
        ('CHARGED', 'Charged'),  # stock deducted + charge created
        ('PAID', 'Paid'),        # payment received
        ('CANCELLED', 'Cancelled'),
    )

    # ===== CONTEXT =====
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    # ===== ROOM / BILLING =====
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Select when charging to room"
    )

    charge_to_room = models.BooleanField(
        default=False,
        help_text="If checked, order is charged to room folio"
    )

    folio = models.ForeignKey(
        Folio,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False
    )

    # ===== FINANCIAL =====
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='OPEN'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"POS-{self.id} ({self.status})"
    
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
    # ==========================================================
    # INTERNAL HELPERS
    # ==========================================================
    def _resolve_folio(self):
        """
        Determine which folio this order belongs to.
        """
        if self.charge_to_room:
            if not self.room:
                raise ValueError("Room must be selected when charging to room.")

            folio = Folio.get_active_room_folio(self.room)
            if not folio:
                raise ValueError("No active folio for selected room.")

            return folio

        # Walk-in customer
        return Folio.objects.create(
            folio_type='WALKIN',
            guest_name='Walk-in Customer'
        )

    # ==========================================================
    # STEP 1: CHARGE ORDER (STOCK + CHARGE)
    # ==========================================================
    @transaction.atomic
    def charge_order(self):
        """
        - Deduct stock ONCE
        - Create Charge ONCE
        - Move order to CHARGED
        """
        if self.status != 'OPEN':
            return  # idempotent safety

        self.folio = self._resolve_folio()
        self.save(update_fields=['folio'])

        total = 0

        for item in self.items.select_related('menu_item__product'):
            line_total = item.quantity * item.price
            total += line_total

            # 🔴 STOCK IS DEDUCTED HERE (ONCE)
            stock_out(
                product=item.menu_item.product,
                department=self.department,
                quantity=item.quantity,
                user=self.created_by,
                reason="Restaurant sale",
                reference=f"POS-{self.id}"
            )

        # 🔴 CHARGE IS CREATED HERE (ONCE)
        Charge.objects.create(
            folio=self.folio,
            description=f"Restaurant Order #{self.id}",
            department=self.department,
            amount=total,
            reference=f"POS-{self.id}"
        )

        self.total_amount = total
        self.status = 'CHARGED'
        self.save(update_fields=['total_amount', 'status'])

    # ==========================================================
    # STEP 2: PAY ORDER (PAYMENT ONLY)
    # ==========================================================
    @transaction.atomic
    def pay_order(self, payment_method='CASH'):
        """
        - Record payment
        - NEVER touches stock
        - NEVER creates charges
        """
        if self.status != 'CHARGED':
            return  # idempotent safety

        Payment.objects.create(
            folio=self.folio,
            amount=self.total_amount,
            method=payment_method,
            collected_by=self.created_by,
            reference=f"POS-{self.id}"
        )

        self.status = 'PAID'
        self.save(update_fields=['status'])
    

    @transaction.atomic
    def refund(self, user, reason=""):
        if self.is_refunded:
            raise ValueError("Order already refunded.")

        # 1️⃣ Restore stock
        for item in self.items.select_related("menu_item__product"):
            stock = Stock.objects.filter(
                product=item.menu_item.product,
                department=self.department
            ).first()

            if stock:
                stock.quantity += item.quantity
                stock.save()

        # 2️⃣ Reverse charge
        Charge.objects.create(
            folio=self.folio,
            description=f"Refund for Order #{self.id}",
            department=self.department,
            amount=-self.total_amount,
            reference=f"REFUND-{self.id}"
        )

        # 3️⃣ Reverse payment if it was paid immediately
        if self.status == "PAID":
            Payment.objects.create(
                folio=self.folio,
                amount=-self.total_amount,
                method="REFUND",
                collected_by=user,
                reference=f"REFUND-{self.id}"
            )

        # 4️⃣ Mark refunded
        self.is_refunded = True
        self.refunded_at = timezone.now()
        self.refunded_by = user
        self.refund_reason = reason
        self.status = "CANCELLED"
        self.save()



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
