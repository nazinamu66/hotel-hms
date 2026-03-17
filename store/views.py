from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from inventory.models import StockMovement
from django.db import models
from kitchen.models import IngredientRestockItem
from decimal import Decimal, InvalidOperation
from core.utils import get_user_hotels
from django.utils.timezone import now
from inventory.models import StockMovement
from accounts.decorators import role_required
from inventory.models import (
    Stock,
    Department,
    StockTransfer,
    LowStockRequest,
    PurchaseOrder,
    PurchaseItem,
)

# =========================
# STORE DASHBOARD
# =========================

@role_required("STORE")
def store_dashboard(request):
    store = request.user.department

    stocks = (
        Stock.objects
        .filter(department=store, product__product_type="RAW")
        .select_related("product")
    )

    low_stocks = stocks.filter(quantity__lte=F("reorder_level"))

    recent_movements = (
        StockMovement.objects
        .filter(
            models.Q(from_department=store) |
            models.Q(to_department=store)
        )
        .select_related("product", "from_department", "to_department")
        .order_by("-created_at")[:10]
    )

    return render(
        request,
        "store/dashboard.html",
        {
            "stocks": stocks,
            "low_stocks": low_stocks,
            "recent_movements": recent_movements,
            "total_items": stocks.count(),
            "low_stock_count": low_stocks.count(),
        }
    )

# =========================
# ISSUE STOCK (STORE → OPS)
# =========================


from decimal import Decimal, InvalidOperation
from django.utils import timezone

@role_required("STORE")
@transaction.atomic
def issue_stock(request):
    store = request.user.department

    stocks = (
        Stock.objects
        .filter(department=store, product__product_type="RAW")
        .select_related("product")
    )

    store = request.user.department
    hotel = store.hotel

    departments = Department.objects.filter(
        hotel=hotel
    ).exclude(department_type="STORE")

    if request.method == "POST":
        product_id = request.POST.get("product_id")
        department_id = request.POST.get("department_id")

        try:
            quantity = Decimal(request.POST.get("quantity"))
        except (TypeError, InvalidOperation):
            messages.error(request, "Invalid quantity.")
            return redirect("store_issue_stock")

        if quantity <= 0:
            messages.error(request, "Quantity must be greater than zero.")
            return redirect("store_issue_stock")

        stock = Stock.objects.select_for_update().filter(
            product_id=product_id,
            department=store
        ).first()

        if not stock:
            messages.error(request, "Item does not exist in store stock.")
            return redirect("store_issue_stock")

        if stock.quantity < quantity:
            messages.error(request, "Insufficient stock available.")
            return redirect("store_issue_stock")

        to_department = get_object_or_404(
            Department,
            pk=department_id
        )

        if to_department == store:
            messages.error(request, "Cannot issue to same department.")
            return redirect("store_issue_stock")

        transfer = StockTransfer.objects.create(
            product=stock.product,
            from_department=store,
            to_department=to_department,
            quantity=quantity,
            created_by=request.user,
        )

        transfer.execute()

        messages.success(request, "Stock issued successfully.")
        return redirect("store_issue_stock")

    return render(
        request,
        "store/issue_stock.html",
        {
            "stocks": stocks,
            "departments": departments,
        }
    )

@role_required("STORE")
def store_issue_history(request):
    store = request.user.department

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    sort = request.GET.get("sort", "-created_at")  # default

    allowed_sorts = {
        "date": "created_at",
        "-date": "-created_at",
        "product": "product__name",
        "-product": "-product__name",
        "quantity": "quantity",
        "-quantity": "-quantity",
        "department": "to_department__name",
        "-department": "-to_department__name",
        "user": "created_by__username",
        "-user": "-created_by__username",
    }

    order_by = allowed_sorts.get(sort, "-created_at")

    movements = StockMovement.objects.filter(
        movement_type="TRANSFER",
        from_department=store
    )

    if date_from and date_to:
        movements = movements.filter(
            created_at__date__range=[date_from, date_to]
        )

    movements = movements.select_related(
        "product",
        "to_department",
        "created_by"
    ).order_by(order_by)

    return render(
        request,
        "store/issue_history.html",
        {
            "movements": movements,
            "date_from": date_from,
            "date_to": date_to,
            "current_sort": sort,
        }
    )

@role_required("STORE")
def store_receive_history(request):
    store = request.user.department

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    sort = request.GET.get("sort", "-date")  # default newest first

    allowed_sorts = {
        "date": "created_at",
        "-date": "-created_at",
        "product": "product__name",
        "-product": "-product__name",
        "quantity": "quantity",
        "-quantity": "-quantity",
        "user": "created_by__username",
        "-user": "-created_by__username",
        "reference": "reference",
        "-reference": "-reference",
    }

    order_by = allowed_sorts.get(sort, "-created_at")

    movements = StockMovement.objects.filter(
        movement_type="IN",
        to_department=store
    )

    if date_from and date_to:
        movements = movements.filter(
            created_at__date__range=[date_from, date_to]
        )

    movements = movements.select_related(
        "product",
        "created_by"
    ).order_by(order_by)

    return render(
        request,
        "store/receive_history.html",
        {
            "movements": movements,
            "date_from": date_from,
            "date_to": date_to,
            "current_sort": sort,
        }
    )


@role_required("STORE")
@transaction.atomic
def issue_request_item(request, item_id):
    store = request.user.department

    item = get_object_or_404(
        IngredientRestockItem.objects.select_related("request", "ingredient"),
        pk=item_id,
        source="STORE"
    )

    # Ensure request is approved
    if item.request.status not in ["APPROVED", "PARTIALLY_ISSUED"]:
        messages.error(request, "Request not approved for issuing.")
        return redirect("store_ingredient_requests")

    stock = Stock.objects.select_for_update().filter(
        product=item.ingredient,
        department=store
    ).first()

    if not stock or stock.quantity <= 0:
        messages.error(request, "No stock available.")
        return redirect("store_ingredient_requests")

    # Calculate remaining
    total_issued = sum(t.quantity for t in item.transfers.all())
    remaining = item.quantity - total_issued

    if remaining <= 0:
        messages.info(request, "Already fully issued.")
        return redirect("store_ingredient_requests")

    issue_quantity = min(stock.quantity, remaining)

    transfer = StockTransfer.objects.create(
        product=item.ingredient,
        from_department=store,
        to_department=item.request.requested_by.department,
        quantity=issue_quantity,
        created_by=request.user,
        ingredient_request_item=item
    )

    transfer.execute()

    messages.success(
        request,
        f"Issued {issue_quantity} of {item.ingredient.name}"
    )

    return redirect("store_ingredient_requests")


from kitchen.models import IngredientRestockRequest
from inventory.models import Stock

@role_required("STORE")
def store_ingredient_requests(request):
    store = request.user.department
    hotel = store.hotel

    requests = (
        IngredientRestockRequest.objects
        .filter(
            status__in=["APPROVED", "PARTIALLY_ISSUED"],
            requested_by__department__hotel=hotel
        )
        .prefetch_related("items__ingredient", "items__transfers")
        .order_by("-created_at")
    )

    for req in requests:
        # attach only STORE items explicitly
        store_items = []

        for item in req.items.filter(source="STORE"):
            total_issued = sum(
                t.quantity for t in item.transfers.all()
            )

            item.total_issued = total_issued
            item.remaining = max(item.quantity - total_issued, 0)

            store_items.append(item)

        # attach computed list to request
        req.store_items = store_items

    return render(
        request,
        "store/ingredient_requests.html",
        {"requests": requests}
    )


# =========================
# LOW STOCK REQUESTS (STORE)
# =========================

@role_required("STORE")
def request_low_stock(request):
    store = request.user.department

    low_stocks = Stock.objects.filter(
        department=store,
        quantity__lte=F("reorder_level")
    ).select_related("product")

    if request.method == "POST":
        product_id = request.POST.get("product_id")
        qty = int(request.POST.get("quantity", 0))

        if qty <= 0:
            messages.error(request, "Invalid quantity.")
            return redirect("store_request_low_stock")

        LowStockRequest.objects.create(
            product_id=product_id,
            department=store,
            requested_quantity=qty,
            requested_by=request.user
        )

        messages.success(request, "Stock request sent.")
        return redirect("store_request_low_stock")

    return render(
        request,
        "store/request_low_stock.html",
        {"stocks": low_stocks}
    )


@role_required("STORE")
def my_stock_requests(request):
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    sort = request.GET.get("sort", "-date")

    allowed_sorts = {
        "date": "created_at",
        "-date": "-created_at",
        "product": "product__name",
        "-product": "-product__name",
        "status": "status",
        "-status": "-status",
        "quantity": "requested_quantity",
        "-quantity": "-requested_quantity",
    }

    order_by = allowed_sorts.get(sort, "-created_at")

    requests = LowStockRequest.objects.filter(
        department=request.user.department,
        requested_by=request.user
    )

    if date_from and date_to:
        requests = requests.filter(
            created_at__date__range=[date_from, date_to]
        )

    requests = requests.select_related("product").order_by(order_by)

    return render(
        request,
        "store/my_requests.html",
        {
            "requests": requests,
            "date_from": date_from,
            "date_to": date_to,
            "current_sort": sort,
        }
    )

@role_required("STORE")
def stock_request_detail(request, pk):
    req = get_object_or_404(
        LowStockRequest,
        pk=pk,
        department=request.user.department,
        requested_by=request.user
    )

    return render(
        request,
        "store/request_detail.html",
        {"request_obj": req}
    )


# =========================
# STORE RECEIVING (PO)
# =========================

@role_required("STORE")
def store_incoming_pos(request):
    pos = PurchaseOrder.objects.filter(
        department=request.user.department,
        status="PAID"
    ).select_related("supplier").order_by("paid_at")

    return render(
        request,
        "store/incoming_pos.html",
        {"pos": pos}
    )

@role_required("STORE", "MANAGER", "ADMIN")
def stock_movement_log(request):
    hotel = get_user_hotels(request.user)

    if request.user.role == "STORE":
        department = request.user.department
        movements = StockMovement.objects.filter(
            models.Q(from_department=department) |
            models.Q(to_department=department)
        )
    else:
        # MANAGER / ADMIN / DIRECTOR
        movements = StockMovement.objects.filter(
            models.Q(from_department__hotel=hotel) |
            models.Q(to_department__hotel=hotel)
        )

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    movement_type = request.GET.get("type")
    sort = request.GET.get("sort", "-date")

    allowed_sorts = {
        "date": "created_at",
        "-date": "-created_at",
        "product": "product__name",
        "-product": "-product__name",
        "quantity": "quantity",
        "-quantity": "-quantity",
        "type": "movement_type",
        "-type": "-movement_type",
    }

    order_by = allowed_sorts.get(sort, "-created_at")

    movements = StockMovement.objects.filter(
        models.Q(from_department=department) |
        models.Q(to_department=department)
    )

    if date_from and date_to:
        movements = movements.filter(
            created_at__date__range=[date_from, date_to]
        )

    if movement_type:
        movements = movements.filter(movement_type=movement_type)

    movements = movements.select_related(
        "product",
        "from_department",
        "to_department",
        "created_by"
    ).order_by(order_by)

    return render(
        request,
        "store/stock_movement_log.html",
        {
            "movements": movements,
            "date_from": date_from,
            "date_to": date_to,
            "movement_type": movement_type,
            "current_sort": sort,
        }
    )

@role_required("STORE", "MANAGER", "ADMIN")
def transfer_detail(request, pk):
    transfer = get_object_or_404(StockTransfer, pk=pk)

    return render(
        request,
        "store/transfer_detail.html",
        {"transfer": transfer}
    )

@role_required("STORE", "MANAGER", "ADMIN")
def daily_stock_report(request):
    today = now().date()

    date_from = request.GET.get("from", today)
    date_to = request.GET.get("to", today)

    hotel = get_user_hotels(request.user)
    movements = StockMovement.objects.filter(
        created_at__date__range=[date_from, date_to]
    )

    if hotel:
        movements = movements.filter(
            models.Q(from_department__hotel=hotel) |
            models.Q(to_department__hotel=hotel)
        )

    return render(
        request,
        "reports/daily_stock_report.html",
        {
            "movements": movements,
            "date_from": date_from,
            "date_to": date_to,
        }
    )
