from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.forms import formset_factory
from decimal import Decimal
from accounts.services.access import (
    get_accessible_hotels,)

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

@role_required("MANAGER", "ADMIN", "DIRECTOR")
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
        .select_related("requested_by")
        .order_by("-created_at")
    )

    # ----------------------------
    # SEARCH
    # ----------------------------
    search = request.GET.get("q", "").strip()

    if search:
        requests = requests.filter(
            Q(requested_by__username__icontains=search)
            | Q(requested_by__first_name__icontains=search)
            | Q(requested_by__last_name__icontains=search)
            | Q(items__ingredient__name__icontains=search)
        ).distinct()

    # ----------------------------
    # STATUS FILTER
    # ----------------------------
    status = request.GET.get("status", "").strip()

    if status:
        requests = requests.filter(status=status)

    # ----------------------------
    # SOURCE FILTER
    # ----------------------------
    source = request.GET.get("source", "").strip()

    if source:
        requests = requests.filter(items__source=source).distinct()

    # ----------------------------
    # SUMMARY COUNTS
    # ----------------------------
    all_requests = IngredientRestockRequest.objects.all()

    total_count = all_requests.count()
    pending_count = all_requests.filter(status="PENDING").count()
    approved_count = all_requests.filter(status="APPROVED").count()
    rejected_count = all_requests.filter(status="REJECTED").count()

    return render(
        request,
        "kitchen/manager/ingredient_requests.html",
        {
            "requests": requests,
            "search": search,
            "status": status,
            "source": source,
            "total_count": total_count,
            "pending_count": pending_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
        },
    )


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def kitchen_ingredient_request_create(request):

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied

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


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR","ACCOUNTANT" )
@transaction.atomic
def kitchen_ingredient_request_detail(request, pk):
    req = get_object_or_404(IngredientRestockRequest, pk=pk)

    # 🔐 Kitchen users see only their own requests
    if request.user.role == "KITCHEN" and req.requested_by != request.user:
        raise PermissionDenied

    is_manager = request.user.role in ["MANAGER", "ADMIN", "DIRECTOR"]
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


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR","ACCOUNTANT")
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

def recipe_ingredients_api(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)

    qty = float(request.GET.get("qty", 1))  # 🔥 NEW

    data = [
        {
            "ingredient_id": item.ingredient.id,
            "ingredient": item.ingredient.name,
            "expected_quantity": float(item.quantity) * qty,  # 🔥 FIX
            "per_unit_quantity": float(item.quantity),        # 🔥 OPTIONAL
            "control_mode": item.control_mode,
            "unit": item.ingredient.base_unit,
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
    user = request.user

    # Resolve the relevant Kitchen department
    if user.role == "KITCHEN":
        kitchen = user.department

    elif user.department:
        kitchen = Department.objects.filter(
            hotel=user.department.hotel,
            department_type="KITCHEN",
            is_active=True,
        ).first()

    else:
        kitchen = Department.objects.filter(
            department_type="KITCHEN",
            is_active=True,
        ).first()

    if not kitchen:
        raise PermissionDenied("No active Kitchen department found.")

    # Product Master is the source of which ingredients belong to Kitchen.
    products = (
        Product.objects
        .filter(
            hotel=kitchen.hotel,
            product_type="RAW",
            is_active=True,
            departments=kitchen,
        )
        .order_by("name")
    )

    # Stock is only the quantity layer.
    stock_map = {
        stock.product_id: stock
        for stock in Stock.objects.filter(
            department=kitchen,
            product__in=products,
        )
    }

    ingredients = []

    for product in products:
        stock = stock_map.get(product.id)

        quantity = stock.quantity if stock else 0
        reorder_level = (
            stock.reorder_level
            if stock
            else product.reorder_level
        )

        if quantity <= 0:
            status = "OUT"
        elif quantity <= reorder_level:
            status = "LOW"
        else:
            status = "OK"

        ingredients.append({
            "product": product,
            "stock": stock,
            "quantity": quantity,
            "status": status,
            "reorder_level": reorder_level,
        })

    available_count = sum(
        1 for item in ingredients
        if item["quantity"] > 0
    )

    can_request = (
        user.role in ("MANAGER", "ADMIN", "DIRECTOR")
        or (
            user.role == "KITCHEN"
            and user.is_department_head
        )
    )

    return render(
        request,
        "kitchen/ingredients.html",
        {
            "ingredients": ingredients,
            "kitchen": kitchen,
            "can_request": can_request,
            "available_count": available_count,

        },
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def prepared_food_list(request):

    user = request.user

    # -----------------------------
    # Resolve Kitchen Department
    # -----------------------------
    if user.role == "KITCHEN":
        kitchen = user.department

    elif user.department:
        kitchen = Department.objects.filter(
            hotel=user.department.hotel,
            department_type="KITCHEN",
            is_active=True,
        ).first()

    else:
        kitchen = Department.objects.filter(
            department_type="KITCHEN",
            is_active=True,
        ).first()

    if not kitchen:
        raise PermissionDenied("No active Kitchen department found.")

    # -----------------------------
    # Prepared foods for this hotel
    # and Kitchen department only
    # -----------------------------
    foods = (
        Product.objects
        .filter(
            hotel=kitchen.hotel,
            product_type="FOOD",
            is_active=True,
            departments=kitchen,
        )
        .order_by("name")
    )

    # -----------------------------
    # Kitchen stock map
    # -----------------------------
    stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=kitchen,
            product__in=foods,
        )
    }

    prepared_foods = []

    for food in foods:
        quantity = stock_map.get(food.id, 0)

        if quantity > 0:
            status = "AVAILABLE"
        else:
            status = "OUT"

        prepared_foods.append({
            "product": food,
            "quantity": quantity,
            "status": status,
        })

    available_count = sum(
        1 for food in prepared_foods
        if food["quantity"] > 0
    )

    total_portions = sum(
        food["quantity"]
        for food in prepared_foods
    )

    can_edit_recipe = (
        user.role in ("MANAGER", "ADMIN", "DIRECTOR")
        or (
            user.role == "KITCHEN"
            and user.is_department_head
        )
    )

    return render(
        request,
        "kitchen/foods/list.html",
        {
            "prepared_foods": prepared_foods,
            "kitchen": kitchen,
            "available_count": available_count,
            "total_portions": total_portions,
            "can_edit_recipe": can_edit_recipe,
        },
    )


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


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT")

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

    hotels = get_accessible_hotels(request.user)

    batches = (
        ProductionBatch.objects
        .select_related(
            "recipe",
            "recipe__product",
            "produced_by",
            "produced_by__department",
        )
    )

    # Kitchen staff can only see their own production history.
    if request.user.role == "KITCHEN":
        batches = batches.filter(
            produced_by=request.user
        )

    # Management can only see production belonging to accessible hotels.
    else:
        batches = batches.filter(
            produced_by__department__hotel__in=hotels
        )

    # -----------------------------
    # Filters
    # -----------------------------
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    recipe_id = request.GET.get("recipe")
    produced_by = request.GET.get("user")

    ordering = request.GET.get("sort", "-created_at")

    allowed_sort = ["created_at", "-created_at"]

    if ordering not in allowed_sort:
        ordering = "-created_at"

    if date_from:
        batches = batches.filter(
            created_at__date__gte=date_from
        )

    if date_to:
        batches = batches.filter(
            created_at__date__lte=date_to
        )

    if recipe_id:
        batches = batches.filter(
            recipe_id=recipe_id
        )

    if produced_by:
        batches = batches.filter(
            produced_by_id=produced_by
        )

    batches = batches.order_by(ordering)

    # -----------------------------
    # Filter options must follow
    # the same hotel scope.
    # -----------------------------
    recipes = Recipe.objects.filter(
        is_active=True,
        product__hotel__in=hotels,
    ).order_by("name")

    users = User.objects.filter(
        role="KITCHEN",
        department__hotel__in=hotels,
        is_active=True,
    ).order_by("username")

    return render(
        request,
        "kitchen/production/history.html",
        {
            "batches": batches,
            "recipes": recipes,
            "users": users,
            "filters": request.GET,
            "ordering": ordering,
        }
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def production_detail(request, pk):

    hotels = get_accessible_hotels(request.user)

    batch = get_object_or_404(
        ProductionBatch.objects
        .select_related(
            "recipe",
            "recipe__product",
            "produced_by",
            "produced_by__department",
        )
        .prefetch_related(
            "ingredient_usages__ingredient"
        ),
        pk=pk,
        produced_by__department__hotel__in=hotels,
    )

    if (
        request.user.role == "KITCHEN"
        and batch.produced_by != request.user
    ):
        raise PermissionDenied

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

    ticket = get_object_or_404(
        KitchenTicket.objects.select_related("order"),
        id=ticket_id,
    )

    if ticket.status == "SERVED":
        messages.warning(
            request,
            "Ticket already served."
        )
        return redirect("kitchen_dashboard")

    if ticket.status != "READY":
        messages.warning(
            request,
            "Only ready tickets can be served."
        )
        return redirect("kitchen_dashboard")

    ticket.status = "SERVED"
    ticket.save(update_fields=["status"])

    messages.success(
        request,
        "Order served."
    )

    return redirect("kitchen_dashboard")