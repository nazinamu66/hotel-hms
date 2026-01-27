from accounts.decorators import role_required
from django.shortcuts import render, redirect
from django.contrib import messages
from inventory.models import Stock, Department, StockTransfer
from django.db.models import F


@role_required("STORE", "MANAGER", "ADMIN")
def store_dashboard(request):
    store = Department.objects.get(name__iexact="Store")

    stocks = Stock.objects.filter(department=store).select_related("product")
    low_stocks = stocks.filter(quantity__lte=F("reorder_level"))

    return render(
        request,
        "store/dashboard.html",
        {
            "stocks": stocks,
            "low_stocks": low_stocks,
        }
    )



@role_required("STORE", "MANAGER", "ADMIN")
def issue_to_kitchen(request):
    store = Department.objects.get(name__iexact="Store")
    kitchen = Department.objects.get(name__iexact="Kitchen")

    stocks = (
        Stock.objects
        .filter(department=store)
        .select_related("product")
        .order_by("product__name")
    )

    if request.method == "POST":
        product_id = request.POST.get("product_id")
        quantity = int(request.POST.get("quantity", 0))

        if quantity <= 0:
            messages.error(request, "Invalid quantity.")
            return redirect("/store/issue/")

        transfer = StockTransfer.objects.create(
            product_id=product_id,
            from_department=store,
            to_department=kitchen,
            quantity=quantity,
            created_by=request.user
        )

        transfer.execute()
        messages.success(request, "Issued to Kitchen successfully.")
        return redirect("/store/issue/")

    return render(
        request,
        "store/issue_to_kitchen.html",
        {"stocks": stocks}
    )


