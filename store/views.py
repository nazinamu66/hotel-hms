from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from inventory.models import StockMovement
from django.db import models
from kitchen.models import IngredientRestockItem
from decimal import Decimal, InvalidOperation
from core.utils import get_user_hotels
from django.utils.timezone import now
from inventory.models import StockMovement,Product
from django.db.models import Q
from accounts.decorators import role_required
from linen.models import LinenRequest, LinenItem
from linen.services.workflows import fulfill_store_request
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

    # ✅ ALL products store can handle
    products = Product.objects.filter(
        departments=store,
        is_active=True
    )

    # ✅ Stock map
    stock_map = {
        s.product_id: s
        for s in Stock.objects.filter(department=store)
    }

    # ✅ Low stock (computed)
    low_stocks = [
        s for s in stock_map.values()
        if s.quantity <= s.reorder_level
    ]

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
            "products": products,
            "stock_map": stock_map,
            "low_stocks": low_stocks,
            "recent_movements": recent_movements,
            "total_items": products.count(),
            "low_stock_count": len(low_stocks),
        }
    )

@role_required("STORE")
def store_stock_requests(request):

    store = request.user.department

    if not store:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    hotel = store.hotel

    requests = (
        LowStockRequest.objects
        .filter(
            department__hotel=hotel,
            department__department_type__in=[
                "HOUSEKEEPING",
                "MAINTENANCE",
                "LAUNDRY",
            ],
            fulfillment_type="STORE",
        )
        .select_related(
            "product",
            "department",
            "department__hotel",
            "requested_by",
        )
        .prefetch_related(
            "transfers",
        )
    )

    # ============================================================
    # SEARCH
    # ============================================================

    search = request.GET.get(
        "search",
        "",
    ).strip()

    if search:

        if search.isdigit():

            requests = requests.filter(
                Q(id=int(search))
                | Q(product__name__icontains=search)
                | Q(department__name__icontains=search)
            )

        else:

            requests = requests.filter(
                Q(product__name__icontains=search)
                | Q(department__name__icontains=search)
            )

    # ============================================================
    # STATUS FILTER
    # ============================================================

    status = request.GET.get(
        "status",
        "",
    ).strip()

    if status in (
        "PENDING",
        "PARTIALLY_FULFILLED",
        "FULFILLED",
        "APPROVED",
        "REJECTED",
    ):

        requests = requests.filter(
            status=status
        )

    # ============================================================
    # DEPARTMENT FILTER
    # ============================================================

    department_type = request.GET.get(
        "department",
        "",
    ).strip()

    if department_type in (
        "HOUSEKEEPING",
        "MAINTENANCE",
        "LAUNDRY",
    ):

        requests = requests.filter(
            department__department_type=department_type
        )

    # ============================================================
    # DATE FILTER
    # ============================================================

    date_from = request.GET.get(
        "from",
        "",
    ).strip()

    date_to = request.GET.get(
        "to",
        "",
    ).strip()

    if date_from:

        requests = requests.filter(
            created_at__date__gte=date_from
        )

    if date_to:

        requests = requests.filter(
            created_at__date__lte=date_to
        )

    # ============================================================
    # SORT
    # ============================================================

    sort = request.GET.get(
        "sort",
        "-created_at",
    ).strip()

    allowed_sorts = {
        "newest": "-created_at",
        "oldest": "created_at",
        "product": "product__name",
        "-product": "-product__name",
        "department": "department__name",
        "-department": "-department__name",
        "status": "status",
        "-status": "-status",
        "quantity": "requested_quantity",
        "-quantity": "-requested_quantity",
    }

    requests = requests.order_by(
        allowed_sorts.get(
            sort,
            "-created_at",
        ),
        "-id",
    )

    # ============================================================
    # CALCULATE FULFILLMENT
    # ============================================================

    for req in requests:

        total_issued = sum(
            Decimal(t.quantity)
            for t in req.transfers.all()
        )

        req.total_issued = total_issued

        req.remaining_quantity = max(
            Decimal(req.requested_quantity) - total_issued,
            Decimal("0"),
        )

    return render(
        request,
        "store/stock_requests.html",
        {
            "requests": requests,
            "search": search,
            "status_filter": status,
            "department_filter": department_type,
            "date_from": date_from,
            "date_to": date_to,
            "sort": sort,
        },
    )

@role_required("STORE")
@transaction.atomic
def store_issue_stock_request(request, pk):

    store = request.user.department

    if not store:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    # ---------------------------------------------------------
    # REQUEST
    # ---------------------------------------------------------

    stock_request = get_object_or_404(
        LowStockRequest.objects
        .select_related(
            "product",
            "department",
            "department__hotel",
            "requested_by",
        )
        .prefetch_related("transfers"),
        pk=pk,
        department__hotel=store.hotel,
        department__department_type__in=[
            "HOUSEKEEPING",
            "MAINTENANCE",
            "LAUNDRY",
        ],
        status__in=[
            "PENDING",
            "APPROVED",
            "PARTIALLY_FULFILLED",
        ],
    )

    # ---------------------------------------------------------
    # CALCULATE ALREADY FULFILLED
    # ---------------------------------------------------------

    total_issued = sum(
        Decimal(t.quantity)
        for t in stock_request.transfers.all()
    )

    requested_quantity = Decimal(
        stock_request.requested_quantity
    )

    remaining_quantity = (
        requested_quantity - total_issued
    )

    if remaining_quantity <= 0:

        stock_request.status = "FULFILLED"
        stock_request.save(
            update_fields=["status"]
        )

        messages.info(
            request,
            "This stock request has already been fulfilled."
        )

        return redirect(
            "store_stock_requests"
        )

    # ---------------------------------------------------------
    # STORE STOCK
    # ---------------------------------------------------------

    stock = (
        Stock.objects
        .select_for_update()
        .filter(
            product=stock_request.product,
            department=store,
        )
        .first()
    )

    available_quantity = (
        stock.quantity
        if stock
        else Decimal("0")
    )

    # ---------------------------------------------------------
    # POST — ISSUE STOCK
    # ---------------------------------------------------------

    if request.method == "POST":

        try:
            issue_quantity = Decimal(
                request.POST.get(
                    "quantity",
                    "0",
                )
            )

        except (TypeError, InvalidOperation):

            messages.error(
                request,
                "Invalid quantity."
            )

            return redirect(
                request.path
            )

        if issue_quantity <= 0:

            messages.error(
                request,
                "Quantity must be greater than zero."
            )

            return redirect(
                request.path
            )

        if issue_quantity > remaining_quantity:

            messages.error(
                request,
                (
                    "You cannot issue more than the "
                    f"remaining requested quantity "
                    f"({remaining_quantity})."
                )
            )

            return redirect(
                request.path
            )

        if issue_quantity > available_quantity:

            messages.error(
                request,
                (
                    "Insufficient Store stock. "
                    f"Available: {available_quantity}."
                )
            )

            return redirect(
                request.path
            )

        # -----------------------------------------------------
        # CREATE TRANSFER
        # -----------------------------------------------------

        transfer = StockTransfer.objects.create(
            product=stock_request.product,
            from_department=store,
            to_department=stock_request.department,
            quantity=int(issue_quantity),
            created_by=request.user,
            stock_request=stock_request,
            note=(
                f"Fulfillment of stock request "
                f"#{stock_request.id}"
            ),
        )

        # -----------------------------------------------------
        # EXECUTE THROUGH EXISTING INVENTORY ENGINE
        # -----------------------------------------------------

        transfer.execute()

        # -----------------------------------------------------
        # UPDATE REQUEST STATUS
        # -----------------------------------------------------

        new_total_issued = (
            total_issued + issue_quantity
        )

        if new_total_issued >= requested_quantity:

            stock_request.status = "FULFILLED"

        else:

            stock_request.status = "PARTIALLY_FULFILLED"

        stock_request.save(
            update_fields=["status"]
        )

        messages.success(
            request,
            (
                f"Issued {issue_quantity} "
                f"{stock_request.product.base_unit} of "
                f"{stock_request.product.name}."
            )
        )

        return redirect(
            "store_stock_requests"
        )

    # ---------------------------------------------------------
    # DISPLAY
    # ---------------------------------------------------------

    return render(
        request,
        "store/issue_stock_request.html",
        {
            "stock_request": stock_request,
            "total_issued": total_issued,
            "remaining_quantity": remaining_quantity,
            "available_quantity": available_quantity,
            "stock": stock,
        },
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
        .filter(
            department=store,
            product__supply_source="STORE"
        )
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


@role_required(
    "STORE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def stock_movement_log(request):

    # --------------------------------------------------------
    # ACCESSIBLE HOTELS
    # --------------------------------------------------------

    hotels = get_user_hotels(
        request.user
    )

    if not hotels.exists():
        raise PermissionDenied

    # --------------------------------------------------------
    # BASE MOVEMENT QUERY
    #
    # STORE:
    #     Only movements involving its own Store department.
    #
    # MANAGEMENT:
    #     Movements involving departments in accessible hotels.
    # --------------------------------------------------------

    if request.user.role == "STORE":

        department = request.user.department

        if not department:
            raise PermissionDenied(
                "You are not assigned to a department."
            )

        movements = StockMovement.objects.filter(
            models.Q(
                from_department=department
            )
            |
            models.Q(
                to_department=department
            )
        )

    else:

        movements = StockMovement.objects.filter(
            models.Q(
                from_department__hotel__in=hotels
            )
            |
            models.Q(
                to_department__hotel__in=hotels
            )
        )

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    search = request.GET.get(
        "search",
        "",
    ).strip()

    if search:

        movements = movements.filter(
            Q(product__name__icontains=search)
            |
            Q(product__sku__icontains=search)
            |
            Q(reference__icontains=search)
            |
            Q(from_department__name__icontains=search)
            |
            Q(to_department__name__icontains=search)
            |
            Q(created_by__username__icontains=search)
        )

    # --------------------------------------------------------
    # DATE FILTER
    # --------------------------------------------------------

    date_from = request.GET.get(
        "from",
        "",
    ).strip()

    date_to = request.GET.get(
        "to",
        "",
    ).strip()

    if date_from:

        movements = movements.filter(
            created_at__date__gte=date_from
        )

    if date_to:

        movements = movements.filter(
            created_at__date__lte=date_to
        )

    # --------------------------------------------------------
    # MOVEMENT TYPE
    # --------------------------------------------------------

    movement_type = request.GET.get(
        "type",
        "",
    ).strip()

    if movement_type in (
        "IN",
        "OUT",
        "TRANSFER",
    ):

        movements = movements.filter(
            movement_type=movement_type
        )

    # --------------------------------------------------------
    # SORTING
    # --------------------------------------------------------

    sort = request.GET.get(
        "sort",
        "-date",
    ).strip()

    allowed_sorts = {
        "-date": "-created_at",
        "date": "created_at",

        "product": "product__name",
        "-product": "-product__name",

        "quantity": "quantity",
        "-quantity": "-quantity",

        "type": "movement_type",
        "-type": "-movement_type",
    }

    order_by = allowed_sorts.get(
        sort,
        "-created_at",
    )

    movements = (
        movements
        .select_related(
            "product",
            "from_department",
            "from_department__hotel",
            "to_department",
            "to_department__hotel",
            "created_by",
        )
        .order_by(
            order_by,
            "-id",
        )
    )

    return render(
        request,
        "store/stock_movement_log.html",
        {
            "movements": movements,

            "search": search,
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

# ============================================================
# LINEN REQUESTS
# ============================================================

@role_required("STORE")
def store_linen_requests(request):

    store = request.user.department

    if not store:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    hotel = store.hotel

    linen_requests = (
        LinenRequest.objects
        .filter(
            linen_item__product__hotel=hotel,
            request_type="STORE",
        )
        .select_related(
            "linen_item",
            "linen_item__product",
            "requested_by",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    search = request.GET.get(
        "search",
        "",
    ).strip()

    if search:

        if search.isdigit():

            linen_requests = linen_requests.filter(
                Q(id=int(search))
                | Q(linen_item__product__name__icontains=search)
                | Q(requested_by__username__icontains=search)
            )

        else:

            linen_requests = linen_requests.filter(
                Q(linen_item__product__name__icontains=search)
                | Q(requested_by__username__icontains=search)
            )

    # --------------------------------------------------------
    # STATUS FILTER
    # --------------------------------------------------------

    status = request.GET.get(
        "status",
        "",
    ).strip()

    allowed_statuses = {
        "PENDING",
        "PARTIAL",
        "FULFILLED",
        "REJECTED",
        "CANCELLED",
    }

    if status in allowed_statuses:

        linen_requests = linen_requests.filter(
            status=status,
        )

    # --------------------------------------------------------
    # CALCULATE REMAINING QUANTITY
    # --------------------------------------------------------

    for linen_request in linen_requests:

        linen_request.remaining_quantity = (
            linen_request.quantity
            - linen_request.fulfilled_quantity
        )

    return render(
        request,
        "store/linen_requests.html",
        {
            "linen_requests": linen_requests,
            "search": search,
            "status_filter": status,
        },
    )

@role_required("STORE")
@transaction.atomic
def store_fulfill_linen_request(request, pk):

    store = request.user.department

    if not store:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    hotel = store.hotel

    linen_request = get_object_or_404(
        LinenRequest.objects.select_related(
            "linen_item",
            "linen_item__product",
            "requested_by",
        ),
        pk=pk,
        request_type="STORE",
        linen_item__product__hotel=hotel,
    )

    if request.method == "POST":

        quantity_raw = request.POST.get(
            "quantity",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        try:
            quantity = int(quantity_raw)

        except (TypeError, ValueError):

            messages.error(
                request,
                "Please enter a valid quantity.",
            )

            return render(
                request,
                "store/fulfill_linen_request.html",
                {
                    "linen_request": linen_request,
                },
            )

        try:

            fulfill_store_request(
                request_obj=linen_request,
                quantity=quantity,
                user=request.user,
                note=note,
            )

        except ValidationError as exc:

            messages.error(
                request,
                str(exc),
            )

            return render(
                request,
                "store/fulfill_linen_request.html",
                {
                    "linen_request": linen_request,
                },
            )

        messages.success(
            request,
            f"{quantity} "
            f"{linen_request.linen_item.product.name} "
            "issued to Housekeeping.",
        )

        return redirect(
            "store_linen_requests",
        )

    return render(
        request,
        "store/fulfill_linen_request.html",
        {
            "linen_request": linen_request,
        },
    )

