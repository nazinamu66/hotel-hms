from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.forms import formset_factory
from decimal import Decimal
from django.core.exceptions import ValidationError

from accounts.decorators import role_required

from inventory.models import (
    Stock,
    Department,
    Product,
    Supplier,
    StockMovement,
    StockTransfer
)

from .models import (
    Recipe,
    RecipeItem,
    ProductionBatch,
    IngredientRestockRequest,
    IngredientRestockItem,
    DirectPurchase,
    DirectPurchaseItem,
    KitchenTicket,
    KitchenTicketItem
)

from .forms import (
    ProductionBatchForm,
    IngredientUsageForm,
    RecipeItemForm,
    RecipeForm,
    IngredientRestockRequestForm,
    IngredientRestockItemFormSet
)

from django.contrib.auth import get_user_model
User = get_user_model()
    
@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def kitchen_ingredient_requests(request):
    requests = (
        IngredientRestockRequest.objects
        .filter(requested_by=request.user)
        .prefetch_related("items__ingredient")
        .select_related("direct_purchase")  # 🔥 updated
        .order_by("-created_at")
    )

    return render(
        request,
        "kitchen/ingredients/requests.html",
        {"requests": requests}
    )

@role_required("KITCHEN")
@transaction.atomic
def direct_purchase_update(request, pk):
    dp = get_object_or_404(
        DirectPurchase,
        pk=pk,
        status="APPROVED"
    )

    if request.method == "POST":
        dp.vendor_name = request.POST.get("vendor")
        total = 0

        for item in dp.items.all():
            cost = Decimal(request.POST.get(f"cost_{item.id}", 0))
            item.unit_cost = cost
            item.save(update_fields=["unit_cost"])
            total += item.line_total()

        dp.total_cost = total
        dp.status = "PURCHASED"
        dp.purchased_at = timezone.now()
        dp.save(update_fields=["vendor_name", "total_cost", "status", "purchased_at"])

        messages.success(request, "Direct purchase recorded.")
        return redirect("kitchen_dashboard")


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_ingredient_requests(request):
    requests = (
        IngredientRestockRequest.objects
        .prefetch_related("items__ingredient")
        .order_by("-created_at")
    )

    return render(
        request,
        "kitchen/manager/ingredient_requests.html",
        {"requests": requests}
    )


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def kitchen_ingredient_request_create(request):

    if request.method == "POST":
        req = IngredientRestockRequest.objects.create(
            requested_by=request.user,
            note=request.POST.get("note", "")
        )

        formset = IngredientRestockItemFormSet(
            request.POST,
            instance=req,
            prefix="items"
        )

        if formset.is_valid():
            formset.save()
            messages.success(request, "Ingredient request submitted.")
            return redirect("kitchen_ingredients")

        request_form = IngredientRestockRequestForm(instance=req)
    else:
        req = IngredientRestockRequest(requested_by=request.user)
        formset = IngredientRestockItemFormSet(instance=req, prefix="items")
        request_form = IngredientRestockRequestForm()

    return render(
        request,
        "kitchen/ingredients/request_create.html",
        {
            "formset": formset,
            "request_form": request_form,
        }
    )


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def kitchen_ingredient_request_detail(request, pk):
    req = get_object_or_404(IngredientRestockRequest, pk=pk)

    # 🔐 Kitchen users see only their own requests
    if request.user.role == "KITCHEN" and req.requested_by != request.user:
        raise PermissionDenied

    is_manager = request.user.role in ["MANAGER", "ADMIN"]
    is_kitchen = request.user.role == "KITCHEN"

    # ============================
    # MANAGER / ADMIN ACTIONS
    # ============================
    if request.method == "POST" and is_manager:

        if req.status != "PENDING":
            messages.warning(request, "This request is already processed.")
            return redirect(request.path)

        action = request.POST.get("action")
        note = request.POST.get("manager_note", "").strip()

        # ---------- REJECT ----------
        if action == "reject":
            req.status = "REJECTED"
            req.note = note
            req.approved_by = request.user
            req.save(update_fields=["status", "note", "approved_by"])

            messages.info(request, "Ingredient request rejected.")
            return redirect("manager_ingredient_requests")

        # ---------- APPROVE ----------
        if action == "approve":
            req.note = note
            req.approved_by = request.user

            # 🔑 DIRECT items → DirectPurchase
            if req.has_direct_items():
                supplier_id = request.POST.get("supplier")
                if not supplier_id:
                    messages.error(request, "Supplier is required for direct purchase.")
                    return redirect(request.path)

                supplier = get_object_or_404(Supplier, id=supplier_id)

                dp = DirectPurchase.objects.create(
                    ingredient_request=req,
                    supplier=supplier,
                    requested_by=req.requested_by,
                    approved_by=request.user,
                    status="APPROVED",
                    note=note,
                    approved_at=timezone.now()
                )

                total = Decimal("0.00")

                for item in req.items.filter(source="DIRECT"):
                    cost = Decimal(request.POST.get(f"cost_{item.id}", "0"))

                    dpi = DirectPurchaseItem.objects.create(
                        purchase=dp,
                        product=item.ingredient,
                        quantity=item.quantity,
                        unit_cost=cost
                    )

                    total += dpi.total_cost

                dp.total_cost = total
                dp.save(update_fields=["total_cost"])

            # 🔥 NO MORE PURCHASE ORDER CREATION HERE

            req.status = "APPROVED"
            req.save(update_fields=["status", "note", "approved_by"])

            messages.success(request, "Ingredient request approved.")
            return redirect("manager_ingredient_requests")

    return render(
        request,
        "kitchen/ingredients/request_detail.html",
        {
            "request_obj": req,
            "is_manager": is_manager,
            "is_kitchen": is_kitchen,
            "has_direct_items": req.has_direct_items(),
            "suppliers": Supplier.objects.all(),  # 🔑 THIS WAS MISSING
        }
    )

@role_required("KITCHEN")
def kitchen_confirm_direct_ingredients(request, pk):
    req = get_object_or_404(IngredientRestockRequest, pk=pk)

    if not hasattr(req, "directpurchase"):
        messages.error(request, "Direct purchase not created yet.")
        return redirect("kitchen_ingredient_requests")

    return redirect("direct_purchase_receive", pk=req.directpurchase.pk)


@role_required("ACCOUNTANT", "DIRECTOR", "ADMIN")
@transaction.atomic
def direct_purchase_pay(request, pk):
    dp = get_object_or_404(
        DirectPurchase,
        pk=pk,
        status="APPROVED"
    )

    if request.method == "POST":
        dp.status = "PAID"
        dp.paid_by = request.user
        dp.paid_at = timezone.now()
        dp.payment_reference = request.POST.get("payment_reference", "")
        dp.save(update_fields=["status", "paid_by", "paid_at", "payment_reference"])

        messages.success(
            request,
            f"Direct Purchase #{dp.id} marked as PAID."
        )
        return redirect("direct_purchase_detail", pk=dp.pk)

    return render(
        request,
        "kitchen/direct_purchase/pay.html",
        {"dp": dp}
    )


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def direct_purchase_list(request):
    qs = DirectPurchase.objects.select_related(
        "supplier", "requested_by"
    ).order_by("-created_at")

    if request.user.role == "KITCHEN":
        qs = qs.filter(requested_by=request.user)
    
    from django.db.models import Sum, F, DecimalField, ExpressionWrapper

    purchases = (
        DirectPurchase.objects
        .select_related("supplier")
        .annotate(
            computed_total=Sum(
                ExpressionWrapper(
                    F("items__quantity") * F("items__unit_cost"),
                    output_field=DecimalField()
                )
            )
        )
    )

    return render(
        request,
        "kitchen/direct_purchase/list.html",
        {"purchases": qs}
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def recipe_ingredients_api(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)

    data = [
        {
            "ingredient_id": item.ingredient.id,
            "ingredient": item.ingredient.name,
            "expected_quantity": float(item.quantity),
            "control_mode": item.control_mode,
            "tolerance_percent": float(item.tolerance_percent),
        }
        for item in recipe.items.select_related("ingredient")
    ]

    return JsonResponse(data, safe=False)


@role_required("KITCHEN")
def kitchen_quick_production(request):

    IngredientFormSet = formset_factory(
        IngredientUsageForm,
        extra=0
    )

    if request.method == "POST":

        batch_form = ProductionBatchForm(request.POST)
        formset = IngredientFormSet(request.POST, prefix="ingredients")

        if batch_form.is_valid() and formset.is_valid():

            recipe = batch_form.cleaned_data["recipe"]
            qty = batch_form.cleaned_data["quantity_produced"]

            if not recipe.items.exists():
                messages.error(request, "Recipe has no ingredients.")
                return redirect("kitchen_quick_production")

            batch = ProductionBatch.objects.create(
                recipe=recipe,
                quantity_produced=qty,
                produced_by=request.user
            )

            actual_quantities = {
                str(f.cleaned_data["ingredient_id"]): f.cleaned_data["actual_quantity"]
                for f in formset
            }

            try:

                batch.execute(actual_quantities=actual_quantities)

                messages.success(
                    request,
                    f"{qty} portions of {recipe.name} produced successfully."
                )

            except ValidationError as e:

                messages.error(
                    request,
                    "Production failed: " + ", ".join(e.messages)
                )

                batch.delete()

            return redirect("kitchen_quick_production")

        messages.error(request, "Invalid production data.")

    else:

        batch_form = ProductionBatchForm()
        formset = IngredientFormSet(prefix="ingredients")

    return render(
        request,
        "kitchen/quick_production.html",
        {
            "batch_form": batch_form,
            "formset": formset,
        }
    )

@role_required("KITCHEN")
def kitchen_dashboard(request):

    tickets = (
        KitchenTicket.objects
        .select_related("order", "room")
        .prefetch_related("items__menu_item")
        .filter(status__in=["NEW", "PREPARING", "READY"])
        .order_by("created_at")
    )

    context = {
        "new_tickets": tickets.filter(status="NEW"),
        "preparing_tickets": tickets.filter(status="PREPARING"),
        "ready_tickets": tickets.filter(status="READY"),
    }

    return render(
        request,
        "kitchen/dashboard.html",
        context
    )


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def kitchen_ingredients(request):
    kitchen = Department.objects.filter(
        department_type="KITCHEN",
        is_active=True
    ).first()

    stocks = Stock.objects.filter(
        department=kitchen,
        product__product_type="RAW"
    ).select_related("product")

    return render(
        request,
        "kitchen/ingredients.html",
        {"stocks": stocks}
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def prepared_food_list(request):

    user = request.user

    # -----------------------------
    # Resolve Kitchen Department
    # -----------------------------
    # user = request.user

# Kitchen staff
    if user.role == "KITCHEN":
        kitchen = user.department

    # Managers / Admin
    elif user.department:
        kitchen = Department.objects.filter(
            hotel=user.department.hotel,
            department_type="KITCHEN",
            is_active=True
        ).first()

    # Director (no department)
    else:
        kitchen = Department.objects.filter(
            department_type="KITCHEN",
            is_active=True
        ).first()

    # -----------------------------
    # All prepared foods
    # -----------------------------
    foods = Product.objects.filter(
        product_type="FOOD",
        is_active=True
    ).order_by("name")

    # -----------------------------
    # Kitchen stock map
    # -----------------------------
    stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=kitchen
        )
    }

    # -----------------------------
    # Render
    # -----------------------------
    return render(
        request,
        "kitchen/foods/list.html",
        {
            "foods": foods,
            "stock_map": stock_map,
            "kitchen": kitchen,
        }
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def manager_ingredient_request_review(request, pk):
    req = get_object_or_404(
        IngredientRestockRequest,
        pk=pk,
        status="PENDING"
    )

    if request.method == "GET":
        return render(
            request,
            "kitchen/manager/review_ingredient_request.html",
            {
                "request_obj": req,
                "suppliers": Supplier.objects.all(),
                "has_direct_items": req.items.filter(source="DIRECT").exists(),
            }
        )

    action = request.POST.get("action")
    note = request.POST.get("manager_note", "").strip()

    # ---------- REJECT ----------
    if action == "reject":
        req.status = "REJECTED"
        req.note = note
        req.approved_by = request.user
        req.save(update_fields=["status", "note", "approved_by"])

        messages.info(request, "Ingredient request rejected.")
        return redirect("manager_ingredient_requests")

    # ---------- APPROVE ----------
    direct_items = req.items.filter(source="DIRECT")

    # 🔥 IMPORTANT:
    # STORE items are NOT converted to PurchaseOrder anymore.
    # Store department will issue stock directly.
    # We only handle DIRECT items here.

    if direct_items.exists():

        supplier_id = request.POST.get("supplier")
        if not supplier_id:
            messages.error(
                request,
                "Supplier is required for direct purchase."
            )
            return redirect(request.path)

        # Validate ALL costs first
        costs = {}
        for item in direct_items:
            cost = request.POST.get(f"cost_{item.id}")
            if not cost:
                messages.error(
                    request,
                    f"Unit cost required for {item.ingredient.name}"
                )
                return redirect(request.path)
            costs[item.id] = Decimal(cost)

        supplier = get_object_or_404(Supplier, pk=supplier_id)

        dp = DirectPurchase.objects.create(
            ingredient_request=req,
            supplier=supplier,
            requested_by=req.requested_by,
            approved_by=request.user,
            note=note,
            status="APPROVED"
        )

        total = Decimal("0.00")

        for item in direct_items:
            dpi = DirectPurchaseItem.objects.create(
                purchase=dp,
                product=item.ingredient,
                quantity=item.quantity,
                unit_cost=costs[item.id]
            )
            total += dpi.total_cost

        dp.total_cost = total
        dp.save(update_fields=["total_cost"])

    # 🔥 Mark request approved (for STORE + DIRECT)
    req.status = "APPROVED"
    req.note = note
    req.approved_by = request.user
    req.save(update_fields=["status", "note", "approved_by"])

    messages.success(request, "Ingredient request approved.")
    return redirect("manager_ingredient_requests")


@role_required("KITCHEN")
@transaction.atomic
def direct_purchase_receive(request, pk):
    dp = get_object_or_404(
        DirectPurchase,
        pk=pk,
        status="PAID"
    )

    if dp.requested_by != request.user:
        raise PermissionDenied

    kitchen = request.user.department
    if kitchen.department_type != "KITCHEN":
        raise PermissionDenied

    for item in dp.items.select_related("product"):
        stock, _ = Stock.objects.get_or_create(
            product=item.product,
            department=kitchen,
            defaults={"quantity": 0}
        )

        stock.quantity += item.quantity
        stock.save(update_fields=["quantity"])

        StockMovement.objects.create(
            product=item.product,
            to_department=kitchen,
            quantity=item.quantity,
            movement_type="IN",
            created_by=request.user,
            reference=f"DP-{dp.id}"
        )

    dp.status = "RECEIVED"
    dp.received_at = timezone.now()
    dp.save(update_fields=["status", "received_at"])

    req = dp.ingredient_request
    req.status = "RECEIVED"
    req.received_at = timezone.now()
    req.save(update_fields=["status", "received_at"])

    messages.success(request, f"Direct Purchase #{dp.id} received.")
    return redirect("kitchen_dashboard")


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def direct_purchase_detail(request, pk):
    dp = get_object_or_404(
        DirectPurchase.objects
        .select_related("supplier", "ingredient_request")
        .prefetch_related("items__product"),
        pk=pk
    )

    if request.user.role == "KITCHEN" and dp.requested_by != request.user:
        raise PermissionDenied

    return render(
        request,
        "kitchen/direct_purchase/detail.html",
        {
            "dp": dp,  # 🔑 FIX
            "can_receive": (
                request.user.role == "KITCHEN"
                and dp.status == "PAID"
            )
        }
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def production_history(request):

    batches = (
        ProductionBatch.objects
        .select_related("recipe", "produced_by")
        .all()
    )

    # Filters
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    recipe_id = request.GET.get("recipe")
    produced_by = request.GET.get("user")

    ordering = request.GET.get("sort", "-created_at")

    allowed_sort = ["created_at", "-created_at"]
    if ordering not in allowed_sort:
        ordering = "-created_at"

    if date_from:
        batches = batches.filter(created_at__date__gte=date_from)

    if date_to:
        batches = batches.filter(created_at__date__lte=date_to)

    if recipe_id:
        batches = batches.filter(recipe_id=recipe_id)

    if produced_by:
        batches = batches.filter(produced_by_id=produced_by)

    batches = batches.order_by(ordering)

    return render(
        request,
        "kitchen/production/history.html",
        {
            "batches": batches,
            "recipes": Recipe.objects.filter(is_active=True),
            "users": User.objects.filter(role__in=["KITCHEN", "MANAGER"]),
            "filters": request.GET,
            "ordering": ordering
        }
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def production_detail(request, pk):

    batch = get_object_or_404(
        ProductionBatch.objects
        .select_related("recipe", "produced_by")
        .prefetch_related("ingredient_usages__ingredient"),
        pk=pk
    )

    return render(
        request,
        "kitchen/production/detail.html",
        {"batch": batch}
    )


@role_required("KITCHEN")
def kitchen_ticket_start(request, ticket_id):

    ticket = get_object_or_404(KitchenTicket, id=ticket_id)

    if ticket.status != "NEW":
        messages.error(request, "Ticket already started.")
        return redirect("kitchen_dashboard")

    ticket.status = "PREPARING"
    ticket.eta_minutes = request.POST.get("eta_minutes")

    ticket.save()

    return redirect("kitchen_dashboard")



@role_required("KITCHEN")
def kitchen_ticket_ready(request, ticket_id):

    ticket = get_object_or_404(KitchenTicket, id=ticket_id)

    ticket.status = "READY"
    ticket.save(update_fields=["status"])

    return redirect("kitchen_dashboard")

@role_required("KITCHEN")
@transaction.atomic
def kitchen_ticket_served(request, ticket_id):

    ticket = get_object_or_404(KitchenTicket, id=ticket_id)

    kitchen = request.user.department

    if ticket.status == "SERVED":
        messages.warning(request, "Ticket already served.")
        return redirect("kitchen_dashboard")
    
    for item in ticket.items.select_related("menu_item__product"):

        product = item.menu_item.product
        qty = item.quantity

        stock = Stock.objects.select_for_update().filter(
            product=product,
            department=kitchen
        ).first()

        if not stock or stock.quantity < qty:
            messages.error(
                request,
                f"Insufficient kitchen stock for {product.name}"
            )
            return redirect("kitchen_dashboard")

        stock.quantity -= qty
        stock.save(update_fields=["quantity"])

        StockMovement.objects.create(
            product=product,
            from_department=kitchen,
            quantity=qty,
            movement_type="OUT",
            created_by=request.user,
            reference=f"KOT-{ticket.id}"
        )

    ticket.status = "SERVED"
    ticket.save(update_fields=["status"])

    messages.success(request, "Order served and kitchen stock updated.")

    return redirect("kitchen_dashboard")