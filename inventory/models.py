from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
# from inventory.models import Department




User = settings.AUTH_USER_MODEL


# =========================
# CORE STRUCTURE
# =========================

from django.db import models
from django.utils.text import slugify


class Hotel(models.Model):

    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    location = models.CharField(max_length=150, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):

        if not self.slug:
            self.slug = slugify(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class HotelFeature(models.Model):

    FEATURE_CHOICES = (
        ("RESTAURANT", "Restaurant"),
        ("KITCHEN", "Kitchen"),
        ("STORE", "Store"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("LAUNDRY", "Laundry"),
        ("GYM", "Gym"),
        ("POOL", "Swimming Pool"),
        ("SPA", "Spa"),
        ("BOUTIQUE", "Boutique Shop"),
    )

    hotel = models.ForeignKey(
        "inventory.Hotel",
        on_delete=models.CASCADE,
        related_name="features"
    )

    feature = models.CharField(
        max_length=30,
        choices=FEATURE_CHOICES
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("hotel", "feature")

    def __str__(self):
        return f"{self.hotel.name} - {self.feature}"
    
from django.db import models
from django.utils.text import slugify


class Department(models.Model):

    DEPARTMENT_TYPES = (
        ("KITCHEN", "Kitchen"),
        ("STORE", "Store"),
        ("RESTAURANT", "Restaurant"),
        ("FRONTDESK", "Front Desk"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("LAUNDRY", "Laundry"),
        ("GYM", "Gym"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="departments",
    )

    name = models.CharField(max_length=100)
    slug = models.SlugField(blank=True)

    department_type = models.CharField(
        max_length=30,
        choices=DEPARTMENT_TYPES
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("hotel", "name")

    def save(self, *args, **kwargs):

        if not self.slug:
            self.slug = slugify(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hotel.name} - {self.name}"    

class Supplier(models.Model):

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):

    PRODUCT_TYPE = (
        ("RAW", "Raw Material"),           # kitchen ingredients
        ("FOOD", "Prepared Food"),        # kitchen finished food
        ("DRINK", "Beverage"),            # restaurant drinks
        ("HOUSEKEEPING", "Housekeeping Supply"),
        ("LAUNDRY", "Laundry Supply"),
        ("BOUTIQUE", "Boutique Item"),
        ("SERVICE", "Service"),
    )

    SUPPLY_SOURCE_CHOICES = (
        ("STORE", "From Store"),
        ("DIRECT", "Direct Purchase"),
    )

    name = models.CharField(
        max_length=150,
        unique=True
    )

    sku = models.CharField(
        max_length=50,
        unique=True
    )

    barcode = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        unique=True
    )

    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE,
        default="RAW"
    )

    base_unit = models.CharField(
        max_length=20,
        help_text="Internal unit (kg, pcs, litre)"
    )

    purchase_unit = models.CharField(
        max_length=20,
        blank=True,
        help_text="Purchase unit (bag, carton, crate)"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    is_active = models.BooleanField(default=True)

    unit_multiplier = models.PositiveIntegerField(
        default=1,
        help_text="How many base units in purchase unit"
    )

    supply_source = models.CharField(
        max_length=10,
        choices=SUPPLY_SOURCE_CHOICES,
        default="STORE"
    )

    reorder_level = models.PositiveIntegerField(
        default=0
    )

    departments = models.ManyToManyField(
        Department,
        help_text="Departments allowed to stock this product",
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["name"]

    # def clean(self):

        # if self.product_type == "RAW" and self.supply_source == "DIRECT":
        #     raise ValidationError(
        #         "Raw materials should normally come from store."
        #     )

    def save(self, *args, **kwargs):

        if self.name:
            self.name = self.name.strip().title()

        if self.sku:
            self.sku = self.sku.strip().upper()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class Stock(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=5)

    class Meta:
        unique_together = ("product", "department")

    def is_low(self):
        return self.quantity <= self.reorder_level

    def __str__(self):
        return f"{self.product} - {self.department}"


# =========================
# PURCHASE ORDERS
# =========================

class PurchaseOrder(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Submitted"),
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
        ("RECEIVED", "Received"),
        ("REJECTED", "Rejected"),
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="approved_pos"
    )
    paid_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="paid_pos"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    payment_reference = models.CharField(max_length=100, blank=True)


    @transaction.atomic
    def receive(self, user):
        print(">>> ENTERING receive()", self.id)

        if self.status == "RECEIVED":
            raise ValidationError("Purchase order already received.")

        if self.status != "PAID":
            raise ValidationError("Only PAID purchase orders can be received.")

        for item in self.items.select_related("product"):
            print("Adding:", item.base_quantity)

            stock_in(
                product=item.product,
                department=self.department,
                quantity=item.base_quantity,
                user=user,
                reference=f"PO-{self.id}"
            )

        self.status = "RECEIVED"
        self.received_at = timezone.now()
        self.save(update_fields=["status", "received_at"])


class PurchaseItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, related_name="items", on_delete=models.CASCADE
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    purchase_quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def base_quantity(self):
        return self.purchase_quantity * self.product.unit_multiplier


# =========================
# STOCK MOVEMENTS
# =========================

class StockMovement(models.Model):
    MOVEMENT_TYPE = (
        ("IN", "Stock In"),
        ("OUT", "Stock Out"),
        ("TRANSFER", "Transfer"),
    )

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    from_department = models.ForeignKey(
        Department, related_name="stock_out",
        on_delete=models.SET_NULL, null=True, blank=True
    )
    to_department = models.ForeignKey(
        Department, related_name="stock_in",
        on_delete=models.SET_NULL, null=True, blank=True
    )

    quantity = models.PositiveIntegerField()
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_TYPE)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.movement_type} - {self.product}"


# =========================
# TRANSFERS & STOCK OUT
# =========================

@transaction.atomic
def transfer_stock(product, from_department, to_department, quantity, user, reference=""):
    if from_department == to_department:
        raise ValidationError("Source and destination cannot be the same.")
    
    if from_department.hotel_id != to_department.hotel_id:
        raise ValidationError("Cross-hotel transfers are not allowed.")

    from_stock = Stock.objects.select_for_update().filter(
        product=product,
        department=from_department
    ).first()

    if not from_stock or from_stock.quantity < quantity:
        raise ValidationError("Insufficient stock.")

    to_stock, _ = Stock.objects.select_for_update().get_or_create(
        product=product,
        department=to_department,
        defaults={"quantity": 0}
    )

    from_stock.quantity -= quantity
    to_stock.quantity += quantity

    from_stock.save()
    to_stock.save()

    StockMovement.objects.create(
        product=product,
        from_department=from_department,
        to_department=to_department,
        quantity=quantity,
        movement_type="TRANSFER",
        created_by=user,
        reference=reference
    )


class StockTransfer(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    from_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="transfer_out")
    to_department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="transfer_in")
    quantity = models.PositiveIntegerField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    ingredient_request_item = models.ForeignKey(
    "kitchen.IngredientRestockItem",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="transfers"
)

    def execute(self):
        transfer_stock(
            product=self.product,
            from_department=self.from_department,
            to_department=self.to_department,
            quantity=self.quantity,
            user=self.created_by,
            reference=f"TRANSFER-{self.id}"
        )

        # 🔑 AUTO-UPDATE ingredient request status
        if self.ingredient_request_item:
            req = self.ingredient_request_item.request
            req.update_issue_status()

    def clean(self):
            if self.from_department.hotel_id != self.to_department.hotel_id:
                raise ValidationError("Cross-hotel transfers are not allowed.")


@transaction.atomic
def stock_out(product, department, quantity, user, reason="", reference=""):
    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    stock = Stock.objects.select_for_update().filter(
        product=product,
        department=department
    ).first()

    if not stock or stock.quantity < quantity:
        raise ValidationError("Insufficient stock.")

    stock.quantity -= quantity
    stock.save()

    StockMovement.objects.create(
        product=product,
        from_department=department,
        quantity=quantity,
        movement_type="OUT",
        created_by=user,
        reference=reference or reason
    )

@transaction.atomic
def stock_in(product, department, quantity, user, reason="", reference=""):

    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")

    stock, _ = Stock.objects.select_for_update().get_or_create(
        product=product,
        department=department,
        defaults={"quantity": 0}
    )

    stock.quantity += quantity
    stock.save(update_fields=["quantity"])

    StockMovement.objects.create(
        product=product,
        to_department=department,
        quantity=quantity,
        movement_type="IN",
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

    def execute(self):
        stock_out(
            product=self.product,
            department=self.department,
            quantity=self.quantity,
            user=self.created_by,
            reason=self.reason,
            reference=f"OUT-{self.id}"
        )


# =========================
# LOW STOCK REQUESTS
# =========================

class LowStockRequest(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("FULFILLED", "Fulfilled"),
        ("REJECTED", "Rejected"),
    )

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)
    requested_quantity = models.PositiveIntegerField()

    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, related_name="stock_requests"
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    manager_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="reviewed_stock_requests"
    )

    purchase_order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_request"
    )
    
    def mark_fulfilled(self):
        self.status = "FULFILLED"
        self.save(update_fields=["status"])

    def __str__(self):
        return f"{self.product} ({self.requested_quantity}) - {self.status}"
    
   