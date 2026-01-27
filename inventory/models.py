from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


class Department(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    unit = models.CharField(max_length=20)  # pcs, kg, litre
    reorder_level = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class Stock(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=5)  # 🔔 NEW

    def is_low(self):
        return self.quantity <= self.reorder_level

    class Meta:
        unique_together = ('product', 'department')

    def __str__(self):
        return f"{self.product} - {self.department}"


from django.db import transaction

class PurchaseOrder(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_received = models.BooleanField(default=False)

    def __str__(self):
        return f"PO-{self.id}"

    @transaction.atomic
    def receive(self, user):
        if self.is_received:
            return  # prevent double receiving

        for item in self.items.all():
            stock, created = Stock.objects.get_or_create(
                product=item.product,
                department=self.department,
                defaults={'quantity': 0}
            )
            stock.quantity += item.quantity
            stock.save()

            StockMovement.objects.create(
                product=item.product,
                to_department=self.department,
                quantity=item.quantity,
                movement_type='IN',
                created_by=user,
                reference=f"PO-{self.id}"
            )

        self.is_received = True
        self.save()
    


class PurchaseItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, related_name='items', on_delete=models.CASCADE
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.product} ({self.quantity})"


class StockMovement(models.Model):
    MOVEMENT_TYPE = (
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('TRANSFER', 'Transfer'),
    )

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    from_department = models.ForeignKey(
        Department, related_name='stock_out', on_delete=models.SET_NULL, null=True, blank=True
    )
    to_department = models.ForeignKey(
        Department, related_name='stock_in', on_delete=models.SET_NULL, null=True, blank=True
    )
    quantity = models.PositiveIntegerField()
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_TYPE)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.movement_type} - {self.product}"
    
    from django.db import transaction
from django.core.exceptions import ValidationError

@transaction.atomic
def transfer_stock(
    product,
    from_department,
    to_department,
    quantity,
    user,
    reference=""
):
    if from_department == to_department:
        raise ValidationError("Source and destination cannot be the same.")

    # Get source stock
    from_stock = Stock.objects.select_for_update().filter(
        product=product,
        department=from_department
    ).first()

    if not from_stock:
        raise ValidationError("No stock available in source department.")

    if from_stock.quantity < quantity:
        raise ValidationError("Insufficient stock to transfer.")

    # Get or create destination stock
    to_stock, _ = Stock.objects.select_for_update().get_or_create(
        product=product,
        department=to_department,
        defaults={'quantity': 0}
    )

    # Update quantities
    from_stock.quantity -= quantity
    to_stock.quantity += quantity

    from_stock.save()
    to_stock.save()

    # Record movement
    StockMovement.objects.create(
        product=product,
        from_department=from_department,
        to_department=to_department,
        quantity=quantity,
        movement_type='TRANSFER',
        created_by=user,
        reference=reference
    )
class StockTransfer(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    from_department = models.ForeignKey(
        Department, related_name='transfer_out', on_delete=models.PROTECT
    )
    to_department = models.ForeignKey(
        Department, related_name='transfer_in', on_delete=models.PROTECT
    )
    quantity = models.PositiveIntegerField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transfer {self.product} ({self.quantity})"

    def execute(self):
        transfer_stock(
            product=self.product,
            from_department=self.from_department,
            to_department=self.to_department,
            quantity=self.quantity,
            user=self.created_by,
            reference=f"TRANSFER-{self.id}"
        )

from django.db import transaction
from django.core.exceptions import ValidationError

@transaction.atomic
def stock_out(
    product,
    department,
    quantity,
    user,
    reason="",
    reference=""
):
    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    stock = Stock.objects.select_for_update().filter(
        product=product,
        department=department
    ).first()

    if not stock:
        raise ValidationError("No stock available for this product.")

    if stock.quantity < quantity:
        raise ValidationError("Insufficient stock.")

    stock.quantity -= quantity
    stock.save()

    StockMovement.objects.create(
        product=product,
        from_department=department,
        quantity=quantity,
        movement_type='OUT',
        created_by=user,
        reference=reference or reason
    )

class StockOut(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"OUT {self.product} ({self.quantity})"

    def execute(self):
        stock_out(
            product=self.product,
            department=self.department,
            quantity=self.quantity,
            user=self.created_by,
            reason=self.reason,
            reference=f"OUT-{self.id}"
        )
