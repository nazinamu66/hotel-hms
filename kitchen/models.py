from django.db import models
from django.conf import settings
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.core.exceptions import ValidationError
from inventory.models import Stock, PurchaseOrder,Supplier
from django.utils import timezone
from inventory.models import (
    Product,
    Department,
    StockMovement,
    StockCostLayer,
    stock_out,
)

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
        
        """
        Return the actual FIFO cost consumed by this production batch.

        ProductionIngredientUsage records quantity and variance for audit.
        The authoritative monetary cost comes from the FIFO allocations
        attached to the production StockMovement.
        """
        movement = (
            StockMovement.objects
            .filter(
                reference=f"PROD-{self.id}",
                movement_type="OUT",
            )
            .prefetch_related("cost_allocations")
            .first()
        )

        if not movement:
            return Decimal("0.00")

        return sum(
            allocation.total_cost
            for allocation in movement.cost_allocations.all()
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
        # Lock the batch itself before checking is_executed. Locking only the
        # stock rows is insufficient: two requests could otherwise both pass
        # the idempotency check and produce the same batch twice.
        batch = (
            self.__class__.objects
            .select_for_update()
            .select_related("recipe__product", "produced_by__department__hotel")
            .get(pk=self.pk)
        )
        self.__dict__.update(batch.__dict__)

        self._validate_producer()

        if not self.recipe.is_active:
            raise ValidationError("Cannot produce inactive recipe.")

        if self.is_executed:
            raise ValidationError("This production batch is already executed.")

        if self.quantity_produced <= 0:
            raise ValidationError("Production quantity must be greater than zero.")

        actual_quantities = actual_quantities or {}
        ingredient_rows = self._collect_ingredient_rows(actual_quantities)

        total_cost = self._deduct_ingredients(ingredient_rows)
        self._add_finished_product(total_cost)
        self._finalize(total_cost)

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

        # Recipes and ingredients must be owned by the same hotel as
        # the kitchen performing production.
        if not self.recipe_id or not self.recipe.product_id:
            raise ValidationError("A recipe with a finished product is required.")

        if self.recipe.product.hotel_id != kitchen.hotel_id:
            raise ValidationError(
                "The recipe's finished product must belong to the kitchen's hotel."
            )

        for recipe_item in self.recipe.items.select_related("ingredient"):
            if recipe_item.ingredient.hotel_id != kitchen.hotel_id:
                raise ValidationError(
                    f"Ingredient {recipe_item.ingredient} belongs to another hotel."
                )
            if not recipe_item.ingredient.departments.filter(pk=kitchen.pk).exists():
                raise ValidationError(
                    f"Ingredient {recipe_item.ingredient} is not assigned to this kitchen."
                )

    # =====================================
    # VALIDATE INGREDIENTS
    # =====================================
    
    def _collect_ingredient_rows(self, actual_quantities):

        kitchen = self.produced_by.department
        ingredient_rows = []

        for item in self.recipe.items.select_related("ingredient"):

            # Recipe standard × number of portions
            expected_qty = (
                Decimal(item.quantity)
                * Decimal(self.quantity_produced)
            )

            # Actual quantity entered by the kitchen.
            # If nothing was supplied, use expected as the fallback.
            raw_actual_qty = actual_quantities.get(
                str(item.ingredient.id),
                actual_quantities.get(item.ingredient.id, expected_qty),
            )
            try:
                actual_qty = Decimal(str(raw_actual_qty))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError(
                    f"Enter a valid actual quantity for {item.ingredient}."
                )

            if not actual_qty.is_finite() or actual_qty <= 0:
                raise ValidationError(
                    f"Actual quantity must be a finite number greater than 0 for "
                    f"{item.ingredient}."
                )

            # Kitchen must have enough working stock.
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
                    f"Insufficient {item.ingredient}."
                )

            # Keep both expected and actual so the production record
            # can retain the variance.
            ingredient_rows.append(
                (item, expected_qty, actual_qty)
            )

        return ingredient_rows

    # =====================================
    # DEDUCT INGREDIENTS
    # =====================================
    def _deduct_ingredients(self, ingredient_rows):

        kitchen = self.produced_by.department

        total_cost = Decimal("0.00")

        for item, expected_qty, actual_qty in ingredient_rows:

            # Production quantities are already expressed in
            # the ingredient's BASE UNIT.
            result = stock_out(
                product=item.ingredient,
                department=kitchen,
                quantity=actual_qty,
                user=self.produced_by,
                reason="Kitchen production",
                reference=f"PROD-{self.id}",
            )

            total_cost += result["total_cost"]

            ProductionIngredientUsage.objects.create(
                production=self,
                ingredient=item.ingredient,
                expected_quantity=expected_qty,
                actual_quantity=actual_qty,
                variance=actual_qty - expected_qty
            )

        return total_cost

    # =====================================
    # ADD FINISHED PRODUCT
    # =====================================
    def _add_finished_product(self, total_cost):

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

        # --------------------------------------
        # CREATE FIFO COST LAYER FOR PREPARED FOOD
        # --------------------------------------

        if total_cost > 0:

            unit_cost = (
                total_cost
                / Decimal(self.quantity_produced)
            )

            StockCostLayer.objects.create(
                product=self.recipe.product,
                department=kitchen,
                purchase_item=None,
                quantity_received=self.quantity_produced,
                quantity_remaining=self.quantity_produced,
                unit_cost=unit_cost,
                is_opening_balance=False,
            )

    # =====================================
    # FINALIZE BATCH
    # =====================================

    def _finalize(self, total_cost):

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

        if total_cost > 0:
            post_journal_entry(
                hotel=hotel,
                description=f"Production #{self.id} - {self.recipe.name}",
                created_by=self.produced_by,
                entry_type="PRODUCTION",
                reference=f"PROD-{self.id}",
                lines=[
                    {"account": finished_inventory, "debit": total_cost},
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
        """
        Recalculate fulfillment from the quantities actually issued from
        Store and the receipt state of any linked Direct Purchase.

        A request is RECEIVED only when every requested source is fulfilled.
        Partial fulfillment is reported even when one line has received
        nothing yet but another line has already been fulfilled.
        """
        if self.status in ("PENDING", "REJECTED"):
            return

        items = list(self.items.all())
        if not items:
            return

        store_items = [item for item in items if item.source == "STORE"]
        direct_items = [item for item in items if item.source == "DIRECT"]

        all_fulfilled = True
        any_fulfilled = False

        for item in store_items:
            total_issued = Decimal("0")
            for transfer in item.transfers.all():
                # A transfer record is only a request/draft until its stock
                # movement exists. Never report unexecuted transfers as issued.
                if StockMovement.objects.filter(
                    reference=f"TRANSFER-{transfer.pk}",
                    movement_type="TRANSFER",
                ).exists():
                    total_issued += transfer.quantity

            line_fulfilled = total_issued >= item.quantity
            if total_issued > 0:
                any_fulfilled = True
            if not line_fulfilled:
                all_fulfilled = False

        if direct_items:
            try:
                direct_purchase = self.direct_purchase
            except DirectPurchase.DoesNotExist:
                direct_purchase = None

            direct_fulfilled = bool(
                direct_purchase and direct_purchase.status == "RECEIVED"
            )
            direct_partially_fulfilled = bool(
                direct_purchase
                and direct_purchase.status == "PARTIALLY_RECEIVED"
            )
            if direct_fulfilled or direct_partially_fulfilled:
                any_fulfilled = True
            if not direct_fulfilled:
                all_fulfilled = False

        if all_fulfilled:
            self.status = "RECEIVED"
            if self.received_at is None:
                self.received_at = timezone.now()
        elif any_fulfilled:
            self.status = "PARTIALLY_ISSUED"
            self.received_at = None
        else:
            self.status = "APPROVED"
            self.received_at = None

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
        ("PARTIALLY_RECEIVED", "Partially Received"),
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
    quantity_received = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal("0.000"),
        help_text="Cumulative quantity received in the product's base unit.",
    )
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def outstanding_quantity(self):
        return max(Decimal("0.000"), self.quantity - self.quantity_received)

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