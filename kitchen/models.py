from django.db import models
from inventory.models import Product, Department
from django.conf import settings

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

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class RecipeItem(models.Model):
    """
    Ingredients required to make ONE unit of a recipe.
    """

    recipe = models.ForeignKey(
        Recipe,
        related_name="items",
        on_delete=models.CASCADE
    )

    ingredient = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        help_text="Ingredient from store stock"
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        help_text="Quantity required per unit"
    )

    def __str__(self):
        return f"{self.ingredient} ({self.quantity}) for {self.recipe}"

from django.db import transaction
from django.core.exceptions import ValidationError
from inventory.models import Stock, StockMovement


class ProductionBatch(models.Model):
    """
    A kitchen production run.
    Deducts ingredients and increases finished food stock.
    """

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

    @transaction.atomic
    def execute(self):
        """
        Executes production:
        - Deduct ingredients from Kitchen
        - Add finished food to Restaurant
        """

        if self.is_executed:
            raise ValidationError("This production batch is already executed.")

        kitchen = Department.objects.get(name__iexact="Kitchen")
        restaurant = Department.objects.get(name__iexact="Restaurant")

        # 1️⃣ Validate ingredient availability
        for item in self.recipe.items.select_related("ingredient"):
            required_qty = item.quantity * self.quantity_produced

            stock = Stock.objects.select_for_update().filter(
                product=item.ingredient,
                department=kitchen
            ).first()

            if not stock or stock.quantity < required_qty:
                raise ValidationError(
                    f"Insufficient {item.ingredient} for production."
                )

        # 2️⃣ Deduct ingredients
        for item in self.recipe.items.select_related("ingredient"):
            required_qty = item.quantity * self.quantity_produced

            stock = Stock.objects.select_for_update().get(
                product=item.ingredient,
                department=kitchen
            )

            stock.quantity -= required_qty
            stock.save()

            StockMovement.objects.create(
                product=item.ingredient,
                from_department=kitchen,
                quantity=required_qty,
                movement_type="OUT",
                created_by=self.produced_by,
                reference=f"PROD-{self.id}"
            )

        # 3️⃣ Add finished food to Restaurant stock
        finished_stock, _ = Stock.objects.select_for_update().get_or_create(
            product=self.recipe.product,
            department=restaurant,
            defaults={"quantity": 0}
        )

        finished_stock.quantity += self.quantity_produced
        finished_stock.save()

        StockMovement.objects.create(
            product=self.recipe.product,
            to_department=restaurant,
            quantity=self.quantity_produced,
            movement_type="IN",
            created_by=self.produced_by,
            reference=f"PROD-{self.id}"
        )

        self.is_executed = True
        self.save(update_fields=["is_executed"])
