from django.db import models
from inventory.models import Product, Department,StockMovement
from django.conf import settings
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from inventory.models import Stock, PurchaseOrder,Supplier
from django.utils import timezone

# from .models import ProductionIngredientUsage

User = settings.AUTH_USER_MODEL


class Recipe(models.Model):
    """
    A finished food item produced by the kitchen
    and sold by the restaurant.
    """

    name = models.CharField(max_length=150, unique=True)

    # Finished product (must be a Product tied to Restaurant stock)
    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        help_text="Finished food product sold by restaurant"
    )

    is_active = models.BooleanField(default=True)

    version = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)

    def total_cost(self):
        return sum(
            item.ingredient.cost_price * item.quantity
            for item in self.items.all()
        )

    def clean(self):
        if self.product.product_type not in ["FOOD", "DRINK"]:
            raise ValidationError(
                "Recipe product must be a FINISHED product."
            )
    
    def __str__(self):
        return self.name

class RecipeItem(models.Model):

    CONTROL_CHOICES = (
        ("STRICT", "Strict (No Variance)"),
        ("TOLERANCE", "Tolerance Allowed"),
        ("FLEXIBLE", "Fully Flexible"),
    )

    recipe = models.ForeignKey(
        Recipe,
        related_name="items",
        on_delete=models.CASCADE
    )

    ingredient = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        limit_choices_to={"product_type": "RAW"}
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity required per unit"
    )

    control_mode = models.CharField(
        max_length=15,
        choices=CONTROL_CHOICES,
        default="STRICT"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    tolerance_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Allowed variance % (only for TOLERANCE mode)"
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recipe", "ingredient"],
                name="unique_ingredient_per_recipe"
            )
        ]

class ProductionBatch(models.Model):

    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.PROTECT
    )

    quantity_produced = models.PositiveIntegerField(
        help_text="Number of units produced"
    )

    produced_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    is_executed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.recipe} x {self.quantity_produced}"

    def total_cost(self):
        return sum(
            usage.actual_quantity * usage.ingredient.cost_price
            for usage in self.ingredient_usages.all()
        )

    def cost_per_unit(self):
        if self.quantity_produced == 0:
            return 0
        return self.total_cost() / self.quantity_produced
    
    def calculate_variance(self):

        variance_data = []

        for item in self.recipe.items.all():

            expected = item.quantity * self.quantity_produced

            actual = sum(
                u.actual_quantity
                for u in self.ingredient_usages.filter(
                    ingredient=item.ingredient
                )
            )

            variance = actual - expected

            variance_data.append({
                "ingredient": item.ingredient.name,
                "expected": expected,
                "actual": actual,
                "variance": variance,
            })

        return variance_data
    
    def calculate_variance_cost(self):

        total_loss = 0

        for item in self.recipe.items.all():

            expected = item.quantity * self.quantity_produced

            actual = sum(
                u.actual_quantity
                for u in self.ingredient_usages.filter(
                    ingredient=item.ingredient
                )
            )

            variance = actual - expected

            if variance > 0:
                total_loss += variance * item.ingredient.cost_price

        return total_loss
    # =====================================
    # EXECUTE PRODUCTION
    # =====================================
    @transaction.atomic
    def execute(self, actual_quantities: dict | None = None):

        self._validate_producer()

        if not self.recipe.is_active:
            raise ValidationError("Cannot produce inactive recipe.")

        if self.is_executed:
            raise ValidationError("This production batch is already executed.")

        actual_quantities = actual_quantities or {}

        ingredient_rows = self._collect_ingredient_rows(actual_quantities)

        self._deduct_ingredients(ingredient_rows)

        self._add_finished_product()

        self._finalize()

    # =====================================
    # VALIDATE PRODUCER
    # =====================================
    def _validate_producer(self):

        if not self.produced_by:
            raise ValidationError("Producing user is required.")

        if self.produced_by.role != "KITCHEN":
            raise ValidationError("Only kitchen staff can execute production.")

        kitchen = self.produced_by.department

        if not kitchen:
            raise ValidationError("Producing user must belong to a department.")

        if kitchen.department_type != "KITCHEN":
            raise ValidationError(
                "Production must be executed from a kitchen department."
            )

    # =====================================
    # VALIDATE INGREDIENTS
    # =====================================
    def _collect_ingredient_rows(self, actual_quantities):

        kitchen = self.produced_by.department
        ingredient_rows = []

        for item in self.recipe.items.select_related("ingredient"):

            expected_qty = Decimal(item.quantity) * Decimal(self.quantity_produced)

            actual_qty = Decimal(
                actual_quantities.get(
                    str(item.ingredient.id),
                    actual_quantities.get(item.ingredient.id, expected_qty)
                )
            )

            if actual_qty <= 0:
                raise ValidationError(
                    f"Actual quantity must be > 0 for {item.ingredient}"
                )

            if item.control_mode == "STRICT":
                if actual_qty != expected_qty:
                    raise ValidationError(
                        f"{item.ingredient} must match recipe exactly."
                    )

            elif item.control_mode == "TOLERANCE":

                allowed_variance = (
                    expected_qty * item.tolerance_percent / Decimal("100")
                )

                if abs(actual_qty - expected_qty) > allowed_variance:
                    raise ValidationError(
                        f"{item.ingredient} exceeds allowed tolerance"
                    )

            stock = (
                Stock.objects
                .select_for_update()
                .filter(
                    product=item.ingredient,
                    department=kitchen
                )
                .first()
            )

            if not stock or stock.quantity < actual_qty:
                raise ValidationError(
                    f"Insufficient {item.ingredient}"
                )

            ingredient_rows.append((item, expected_qty, actual_qty))

        return ingredient_rows

    # =====================================
    # DEDUCT INGREDIENTS
    # =====================================
    def _deduct_ingredients(self, ingredient_rows):

        kitchen = self.produced_by.department

        for item, expected_qty, actual_qty in ingredient_rows:

            stock = Stock.objects.select_for_update().get(
                product=item.ingredient,
                department=kitchen
            )

            stock.quantity -= actual_qty
            stock.save(update_fields=["quantity"])

            StockMovement.objects.create(
                product=item.ingredient,
                from_department=kitchen,
                quantity=actual_qty,
                movement_type="OUT",
                created_by=self.produced_by,
                reference=f"PROD-{self.id}"
            )

            ProductionIngredientUsage.objects.create(
                production=self,
                ingredient=item.ingredient,
                expected_quantity=expected_qty,
                actual_quantity=actual_qty,
                variance=actual_qty - expected_qty
            )

    # =====================================
    # ADD FINISHED PRODUCT
    # =====================================
    def _add_finished_product(self):

        kitchen = self.produced_by.department

        finished_stock, _ = (
            Stock.objects
            .select_for_update()
            .get_or_create(
                product=self.recipe.product,
                department=kitchen,
                defaults={"quantity": 0}
            )
        )

        finished_stock.quantity += self.quantity_produced
        finished_stock.save(update_fields=["quantity"])

        StockMovement.objects.create(
            product=self.recipe.product,
            to_department=kitchen,
            quantity=self.quantity_produced,
            movement_type="IN",
            created_by=self.produced_by,
            reference=f"PROD-{self.id}"
        )

    # =====================================
    # FINALIZE BATCH
    # =====================================

    def _finalize(self):

        # --------------------------------------
        # ACCOUNTING ENTRY (RAW → FINISHED)
        # --------------------------------------
        from accounting.services.journal import post_journal_entry
        from accounting.models import Account

        hotel = self.produced_by.department.hotel

        raw_inventory = Account.objects.get(
            hotel=hotel,
            slug="inventory-asset"
        )

        finished_inventory = Account.objects.get(
            hotel=hotel,
            slug="finished-goods-inventory"
        )

        # 🔥 total cost of ingredients used
        total_cost = sum(
            usage.actual_quantity * usage.ingredient.cost_price
            for usage in self.ingredient_usages.all()
        )

        if total_cost > 0:
            post_journal_entry(
                hotel=hotel,
                description=f"Production #{self.id} - {self.recipe.name}",
                created_by=self.produced_by,
                lines=[
                    # ✅ ADD finished goods
                    {"account": finished_inventory, "debit": total_cost},

                    # ✅ REMOVE raw materials
                    {"account": raw_inventory, "credit": total_cost},
                ]
            )

        # --------------------------------------
        # FINALIZE
        # --------------------------------------
        self.is_executed = True
        self.save(update_fields=["is_executed"])

class ProductionIngredientUsage(models.Model):
    """
    Audit log of ingredients actually used during a production batch.
    Immutable once created.
    """

    production = models.ForeignKey(
        "ProductionBatch",
        related_name="ingredient_usages",
        on_delete=models.CASCADE
    )

    ingredient = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )

    expected_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity expected based on recipe"
    )

    actual_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity actually used by kitchen"
    )

    variance = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="actual - expected"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ingredient__name"]

    def __str__(self):
        return (
            f"{self.ingredient} | "
            f"Exp: {self.expected_quantity} | "
            f"Act: {self.actual_quantity}"
        )

# kitchen/models.py
class IngredientRestockRequest(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("PARTIALLY_ISSUED", "Partially Issued"),   # 🔑 NEW
        ("REJECTED", "Rejected"),
        ("RECEIVED", "Received"),
    )

    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING"
    )

    note = models.TextField(blank=True)

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_ingredient_requests"
    )

    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def update_issue_status(self):
        store_items = self.items.filter(source="STORE")

        if not store_items.exists():
            return  # nothing to track

        fully_issued = True
        partially_issued = False

        for item in store_items:
            total_issued = sum(
                Decimal(t.quantity) for t in item.transfers.all()
            )

            if total_issued == 0:
                fully_issued = False
            elif total_issued < item.quantity:
                fully_issued = False
                partially_issued = True

        if fully_issued:
            self.status = "RECEIVED"
            self.received_at = timezone.now()
        elif partially_issued:
            self.status = "PARTIALLY_ISSUED"
        else:
            self.status = "APPROVED"

        self.save(update_fields=["status", "received_at"])


    def has_direct_items(self):
        return self.items.filter(source="DIRECT").exists()

    def has_store_items(self):
        return self.items.filter(source="STORE").exists()
    


class IngredientRestockItem(models.Model):
    SOURCE_CHOICES = (
        ("STORE", "From Store"),
        ("DIRECT", "Direct Purchase"),
    )

    request = models.ForeignKey(
        IngredientRestockRequest,
        related_name="items",
        on_delete=models.CASCADE
    )

    ingredient = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        limit_choices_to={"product_type": "RAW"}
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3
    )

    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES
    )

    def __str__(self):
        return f"{self.ingredient} ({self.quantity})"

class DirectPurchase(models.Model):
    STATUS_CHOICES = (
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
        ("RECEIVED", "Received"),
        ("REJECTED", "Rejected"),
    )

    ingredient_request = models.OneToOneField(
        "IngredientRestockRequest",
        on_delete=models.CASCADE,
        related_name="direct_purchase",
        null=True,
        blank=True
    )


    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT
    )

    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="direct_purchases"
    )

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_direct_purchases"
    )

    paid_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paid_direct_purchases"
    )

    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="APPROVED"
    )

    note = models.TextField(blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Direct Purchase #{self.id} – {self.supplier} ({self.status})"

class DirectPurchaseItem(models.Model):
    purchase = models.ForeignKey(
        DirectPurchase,
        related_name="items",
        on_delete=models.CASCADE
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        limit_choices_to={"product_type": "RAW"}
    )

    quantity = models.DecimalField(max_digits=10, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

class KitchenTicket(models.Model):

    STATUS_CHOICES = (
        ("NEW", "New"),
        ("PREPARING", "Preparing"),
        ("READY", "Ready"),
        ("SERVED", "Served"),
    )

    order = models.OneToOneField(
        "restaurant.POSOrder",
        on_delete=models.CASCADE,
        related_name="kitchen_ticket"
    )

    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="NEW"
    )

    accepted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    
    eta_minutes = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"KOT-{self.id} (Order {self.order.id})"


class KitchenTicketItem(models.Model):

    ticket = models.ForeignKey(
        KitchenTicket,
        related_name="items",
        on_delete=models.CASCADE
    )

    menu_item = models.ForeignKey(
        "restaurant.MenuItem",
        on_delete=models.PROTECT
    )

    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"