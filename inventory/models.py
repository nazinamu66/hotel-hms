from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify
from decimal import Decimal



# from inventory.models import Department




User = settings.AUTH_USER_MODEL

class Organization(models.Model):

    name = models.CharField(
        max_length=150,
        unique=True,
    )

    slug = models.SlugField(
        max_length=150,
        unique=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def save(self, *args, **kwargs):

        if not self.slug:
            self.slug = slugify(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# =========================
# CORE STRUCTURE
# =========================

from django.db import models
from django.utils.text import slugify


class Hotel(models.Model):

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="hotels",
        null=True,
        blank=True,
    )

    name = models.CharField(
        max_length=150,
        unique=True,
    )

    slug = models.SlugField(
        unique=True,
        blank=True,
    )

    location = models.CharField(
        max_length=150,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

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
        ("WIFI", "Wi-Fi Management"),
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

        # ---------------- Operations ----------------

        ("FRONTDESK", "Front Desk"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("RESTAURANT", "Restaurant"),
        ("KITCHEN", "Kitchen"),
        ("STORE", "Store"),
        ("LAUNDRY", "Laundry"),
        ("GYM", "Gym"),

        # ---------------- Administration ----------------

        ("ACCOUNTING", "Accounting"),
        ("ADMINISTRATION", "Administration"),
        ("HUMAN_RESOURCES", "Human Resources"),
        ("MAINTENANCE", "Maintenance"),
        ("SECURITY", "Security"),
        ("IT", "Information Technology"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="departments",
    )

    code = models.CharField(
        max_length=10,
    )

    name = models.CharField(max_length=100)

    slug = models.SlugField(blank=True)

    department_type = models.CharField(
        max_length=30,
        choices=DEPARTMENT_TYPES,
    )

    is_active = models.BooleanField(default=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "code"],
                name="unique_department_code_per_hotel",
            ),
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_department_name_per_hotel",
            ),
        ]
        ordering = ["hotel__name", "name"]

class Supplier(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="suppliers",
    )

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

    # -----------------------------
    # PRODUCT CLASSIFICATION
    # -----------------------------
    PRODUCT_TYPE = (
        ("RAW", "Raw Material"),
        ("FOOD", "Prepared Food"),
        ("DRINK", "Beverage"),
        ("HOUSEKEEPING", "Housekeeping Supply"),
        ("LAUNDRY", "Laundry Supply"),
        ("BOUTIQUE", "Boutique Item"),
        ("SERVICE", "Service"),
    )

    # 🔥 NEW (CRITICAL)
    USAGE_TYPE = (
        ("CONSUMABLE", "Consumable"),     # sugar, soap, tea
        ("RESALE", "Resale Item"),        # drinks, boutique
        ("INTERNAL", "Internal Use"),     # cleaning, ops
        ("ASSET", "Asset"),               # towels, bedsheets
    )

    SUPPLY_SOURCE_CHOICES = (
        ("STORE", "From Store"),
        ("DIRECT", "Direct Purchase"),
    )

    # -----------------------------
    # BASIC INFO
    # -----------------------------
    name = models.CharField(max_length=150)

    sku = models.CharField(
        max_length=50,
    )

    barcode = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )
    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.PROTECT,
        related_name="products",
    )

    # -----------------------------
    # CLASSIFICATION
    # -----------------------------
    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE,
        default="RAW"
    )

    usage_type = models.CharField(
        max_length=20,
        choices=USAGE_TYPE,
        default="CONSUMABLE"
    )

    supply_source = models.CharField(
        max_length=10,
        choices=SUPPLY_SOURCE_CHOICES,
        default="STORE"
    )

    # -----------------------------
    # UNITS & PRICING
    # -----------------------------
    base_unit = models.CharField(
        max_length=20,
        help_text="Internal unit (kg, pcs, litre)"
    )

    purchase_unit = models.CharField(
        max_length=20,
        blank=True,
        help_text="Purchase unit (bag, carton, crate)"
    )

    purchase_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Cost per purchase unit (carton, bag, crate)"
    )

    unit_multiplier = models.PositiveIntegerField(
        default=1,
        help_text="How many base units in purchase unit"
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )
    # -----------------------------
    # CONTROL
    # -----------------------------
    reorder_level = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    departments = models.ManyToManyField(
        "inventory.Department",
        blank=True,
        help_text="Departments allowed to use/request this product"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # -----------------------------
    # META
    # -----------------------------
    class Meta:
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_hotel_product_name",
            ),
            models.UniqueConstraint(
                fields=["hotel", "sku"],
                name="unique_hotel_product_sku",
            ),
            models.UniqueConstraint(
                fields=["hotel", "barcode"],
                name="unique_hotel_product_barcode",
            ),
        ]

    # -----------------------------
    # VALIDATION
    # -----------------------------
    def clean(self):

        # ----------------------------------
        # RAW MATERIAL RULES
        # ----------------------------------
        if self.product_type == "RAW":

            if self.supply_source == "DIRECT":
                raise ValidationError("Raw materials should normally come from store.")

            if self.cost_price <= 0:
                raise ValidationError("Raw materials must have cost price.")

        # ----------------------------------
        # FINISHED FOOD RULES
        # ----------------------------------
        if self.product_type == "FOOD":

            # 🔥 FOOD is always produced, not purchased
            self.supply_source = "STORE"

            # 🔥 cost comes from recipe, NOT manual
            self.cost_price = 0

            # 🔥 must be resale
            if self.usage_type != "RESALE":
                raise ValidationError("Prepared food must be RESALE.")

        # ----------------------------------
        # DRINK / BOUTIQUE RULES
        # ----------------------------------
        if self.product_type in ["DRINK", "BOUTIQUE"]:

            if self.usage_type != "RESALE":
                raise ValidationError("Sellable items must be RESALE.")

            if self.cost_price <= 0:
                raise ValidationError("Resale items must have cost price.")

        # ----------------------------------
        # SERVICE RULES
        # ----------------------------------
        if self.product_type == "SERVICE":

            if self.usage_type != "INTERNAL":
                raise ValidationError("Service items must be INTERNAL.")

    # -----------------------------
    # SAVE
    # -----------------------------
    def save(self, *args, **kwargs):

        if self.name:
            self.name = self.name.strip().title()

        if self.sku:
            self.sku = self.sku.strip().upper()

        if not self.barcode:
            self.barcode = slugify(self.sku)

        # 🔥 AUTO COST NORMALIZATION
        if self.unit_multiplier > 0 and self.purchase_cost:
            self.cost_price = self.purchase_cost / self.unit_multiplier

        super().save(*args, **kwargs)

    # -----------------------------
    # HELPERS
    # -----------------------------
    def is_stock_item(self):
        """
        Only consumable + resale affect stock
        """
        return self.usage_type in ["CONSUMABLE", "RESALE"]
    def is_operational_stock_item(self):
        """
        INTERNAL products are hotel operating supplies
        consumed by departments rather than sold through POS.
        """
        return self.usage_type == "INTERNAL"
    def to_base_unit(self, quantity):
        """
        Convert purchase unit → base unit
        """
        return quantity * self.unit_multiplier


    def from_base_unit(self, quantity):
        """
        Convert base unit → purchase unit
        """
        if self.unit_multiplier == 0:
            return quantity
        return quantity / self.unit_multiplier

    def is_sellable(self):
        return self.usage_type == "RESALE"

    def is_asset(self):
        return self.usage_type == "ASSET"

    def __str__(self):
        return self.name
    
  

class Stock(models.Model):

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT
    )

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    reorder_level = models.PositiveIntegerField(default=5)

    class Meta:
        unique_together = ("product", "department")

    # -----------------------------
    # LOGIC
    # -----------------------------
    def is_low(self):
        return self.quantity <= self.reorder_level

    def can_consume(self, qty):
        return self.quantity >= qty

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
        ("RECEIVED", "Received"),
        ("REJECTED", "Rejected"),
    )

    PAYMENT_STATUS_CHOICES = (
        ("UNPAID", "Unpaid"),
        ("PARTIAL", "Partially Paid"),
        ("PAID", "Paid"),
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="DRAFT",
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default="UNPAID",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
    )

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_pos",
    )

    paid_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paid_pos",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    payment_reference = models.CharField(
        max_length=100,
        blank=True,
    )

    @transaction.atomic
    def receive(self, user):

        from accounting.services.postings.purchase import post_inventory_receipt

        print(">>> ENTERING receive()", self.id)

        if self.status == "RECEIVED":
            raise ValidationError("Purchase order already received.")

        if self.status not in ("APPROVED",):
            raise ValidationError(
                "Only approved purchase orders can be received."
            )

        for item in self.items.select_related("product"):

            stock_in(
                product=item.product,
                department=self.department,
                quantity=item.purchase_quantity,
                user=user,
                reference=f"PO-{self.id}",
            )

        self.status = "RECEIVED"
        self.received_at = timezone.now()
        self.save(update_fields=["status", "received_at"])

        # ✅ FIXED
        post_inventory_receipt(self)


class PurchaseItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, related_name="items", on_delete=models.CASCADE
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    purchase_quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def total_cost(self):
        return self.purchase_quantity * self.unit_cost

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

    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
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
def transfer_stock(
    product,
    from_department,
    to_department,
    quantity,
    user,
    reference="",
):
    """
    Transfer stock between departments.

    IMPORTANT:
    quantity is always expressed in the product's BASE UNIT.

    Example:
        Detergent:
            purchase unit = carton
            base unit = litre
            multiplier = 20

        Transferring 5 litres means:
            quantity=5

        NOT:
            quantity=5 cartons
    """

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    if from_department == to_department:
        raise ValidationError(
            "Source and destination cannot be the same."
        )

    if from_department.hotel_id != to_department.hotel_id:
        raise ValidationError(
            "Cross-hotel transfers are not allowed."
        )

    from_stock = (
        Stock.objects
        .select_for_update()
        .filter(
            product=product,
            department=from_department,
        )
        .first()
    )

    if not from_stock:
        raise ValidationError(
            f"No stock record exists for {product.name} "
            f"in {from_department.name}."
        )

    if from_stock.quantity < quantity:
        raise ValidationError(
            f"Insufficient stock for {product.name}."
        )

    to_stock, _ = (
        Stock.objects
        .select_for_update()
        .get_or_create(
            product=product,
            department=to_department,
            defaults={
                "quantity": 0,
            },
        )
    )

    from_stock.quantity -= quantity
    to_stock.quantity += quantity

    from_stock.save(
        update_fields=["quantity"]
    )

    to_stock.save(
        update_fields=["quantity"]
    )

    StockMovement.objects.create(
        product=product,
        from_department=from_department,
        to_department=to_department,
        quantity=quantity,
        movement_type="TRANSFER",
        created_by=user,
        reference=reference,
    )


class StockTransfer(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )

    from_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="transfer_out"
    )

    to_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="transfer_in"
    )

    quantity = models.PositiveIntegerField()

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    note = models.TextField(
        blank=True
    )

    ingredient_request_item = models.ForeignKey(
        "kitchen.IngredientRestockItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfers"
    )

    stock_request = models.ForeignKey(
        "LowStockRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfers",
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
def stock_out(
    product,
    department,
    quantity,
    user,
    reason="",
    reference="",
):
    """
    Remove stock from a department.

    IMPORTANT:
    quantity is always expressed in the product's BASE UNIT.

    Example:
        Detergent base unit = litre

        quantity=3
        means 3 litres.
    """

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    stock = (
        Stock.objects
        .select_for_update()
        .filter(
            product=product,
            department=department,
        )
        .first()
    )

    if not stock:
        raise ValidationError(
            f"No stock record exists for {product.name} "
            f"in {department.name}."
        )

    if stock.quantity < quantity:
        raise ValidationError(
            f"Insufficient stock for {product.name}."
        )

    stock.quantity -= quantity

    stock.save(
        update_fields=["quantity"]
    )

    StockMovement.objects.create(
        product=product,
        from_department=department,
        quantity=quantity,
        movement_type="OUT",
        created_by=user,
        reference=reference or reason,
    )

@transaction.atomic
def stock_in(
    product,
    department,
    quantity,
    user,
    reason="",
    reference="",
):

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    stock, _ = Stock.objects.select_for_update().get_or_create(
        product=product,
        department=department,
        defaults={"quantity": 0},
    )

    base_qty = product.to_base_unit(
        quantity,
    )

    stock.quantity += base_qty

    stock.save(
        update_fields=[
            "quantity",
        ]
    )

    print(
        "DEBUG STOCK IN:",
        "quantity=", quantity,
        "base_qty=", base_qty,
        "multiplier=", product.unit_multiplier,
    )

    StockMovement.objects.create(
        product=product,
        to_department=department,
        quantity=base_qty,
        movement_type="IN",
        created_by=user,
        reference=reference or reason,
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

    FULFILLMENT_CHOICES = (
        ("STORE", "From Store"),
        ("PURCHASE", "Purchase"),
    )

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("PARTIALLY_FULFILLED", "Partially Fulfilled"),
        ("FULFILLED", "Fulfilled"),
        ("REJECTED", "Rejected"),
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT
    )

    requested_quantity = models.PositiveIntegerField()

    fulfillment_type = models.CharField(
        max_length=10,
        choices=FULFILLMENT_CHOICES,
        default="STORE",
    )

    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_requests"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING"
    )

    # Kept for compatibility with the existing
    # PO / manager workflow.
    manager_note = models.TextField(
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    # Kept for the existing PO workflow.
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_stock_requests"
    )

    # Existing PO capability remains intact.
    purchase_order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_request"
    )

    def update_fulfillment_status(self):
        """
        Update Store fulfillment status based on the quantity
        transferred against this request.

        Only STORE requests use StockTransfer-based fulfillment.
        PURCHASE requests continue through the existing PO workflow.
        """

        if self.fulfillment_type != "STORE":
            return

        total_issued = sum(
            Decimal(t.quantity)
            for t in StockTransfer.objects.filter(
                stock_request=self
            )
        )

        requested = Decimal(
            self.requested_quantity
        )

        if total_issued <= 0:
            new_status = "PENDING"

        elif total_issued < requested:
            new_status = "PARTIALLY_FULFILLED"

        else:
            new_status = "FULFILLED"

        if self.status != new_status:
            self.status = new_status
            self.save(
                update_fields=["status"]
            )

    def mark_fulfilled(self):
        self.status = "FULFILLED"

        self.save(
            update_fields=["status"]
        )

    def __str__(self):
        return (
            f"{self.product} "
            f"({self.requested_quantity}) "
            f"- {self.status}"
        )
class StockAdjustment(models.Model):

    ADJUSTMENT_TYPE = (
        ("LOSS", "Loss / Spoilage"),
        ("DAMAGE", "Damage"),
        ("COUNT", "Stock Count Correction"),
    )

    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    department = models.ForeignKey("Department", on_delete=models.CASCADE)

    quantity = models.DecimalField(max_digits=10, decimal_places=2)

    adjustment_type = models.CharField(max_length=10, choices=ADJUSTMENT_TYPE)

    reason = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.quantity} ({self.adjustment_type})"