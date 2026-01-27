from django.shortcuts import render, redirect
from django.contrib import messages
from accounts.decorators import role_required
from inventory.models import Stock, Department
from .models import Recipe, ProductionBatch
from django.db.models import F


@role_required("KITCHEN", "MANAGER", "ADMIN")
def kitchen_stock_dashboard(request):
    kitchen = Department.objects.get(name__iexact="Kitchen")

    stocks = (
        Stock.objects
        .filter(department=kitchen)
        .select_related("product")
        .order_by("product__name")
    )

    low_stocks = stocks.filter(quantity__lte=F("reorder_level"))

    return render(
        request,
        "kitchen/stock_dashboard.html",
        {
            "stocks": stocks,
            "low_stocks": low_stocks,
        }
    )



@role_required("KITCHEN", "MANAGER", "ADMIN")
def kitchen_production(request):
    kitchen = Department.objects.get(name__iexact="Kitchen")
    restaurant = Department.objects.get(name__iexact="Restaurant")

    recipes = Recipe.objects.filter(is_active=True).select_related("product")

    # Build restaurant stock map
    restaurant_stock = {
        s.product_id: s.quantity
        for s in Stock.objects.filter(department=restaurant)
    }

    if request.method == "POST":
        recipe_id = request.POST.get("recipe_id")
        qty = int(request.POST.get("quantity", 0))

        if qty <= 0:
            messages.error(request, "Quantity must be greater than zero.")
            return redirect("kitchen_produce")

        recipe = Recipe.objects.get(id=recipe_id)

        batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=qty,
            produced_by=request.user,
        )

        try:
            batch.execute()
            messages.success(
                request,
                f"Produced {qty} units of {recipe.name}."
            )
        except Exception as e:
            messages.error(request, str(e))

        return redirect("kitchen_produce")

    context = {
        "recipes": recipes,
        "restaurant_stock": restaurant_stock,
    }

    return render(
        request,
        "kitchen/produce.html",
        context
    )

@role_required("KITCHEN", "MANAGER", "ADMIN")
def kitchen_stock_dashboard(request):
    kitchen = Department.objects.get(name__iexact="Kitchen")

    stocks = (
        Stock.objects
        .filter(department=kitchen)
        .select_related("product")
        .order_by("product__name")
    )

    context = {
        "stocks": stocks
    }

    return render(
        request,
        "kitchen/stock_dashboard.html",
        context
    )
