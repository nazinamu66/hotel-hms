from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.forms import formset_factory
from decimal import Decimal, InvalidOperation
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
    StockCostLayer,
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
@require_POST
@transaction.atomic
def direct_purchase_update(request, pk):
    hotels = get_accessible_hotels(request.user)
    dp = get_object_or_404(
        DirectPurchase.objects.select_for_update().select_related("supplier"),
        pk=pk,
        status="APPROVED",
        supplier__hotel__in=hotels,
    )

    items = list(dp.items.select_for_update().all())
    if not items:
        messages.error(request, "This direct purchase has no items.")
        return redirect("direct_purchase_detail", pk=dp.pk)

    total = Decimal("0.00")
    for item in items:
        raw_cost = request.POST.get(f"cost_{item.pk}", "").strip()
        try:
            cost = Decimal(raw_cost)
        except (InvalidOperation, TypeError):
            messages.error(request, f"Enter a valid unit cost for {item.product.name}.")
            return redirect("direct_purchase_detail", pk=dp.pk)

        if not cost.is_finite() or cost < 0:
            messages.error(request, f"Unit cost for {item.product.name} cannot be negative.")
            return redirect("direct_purchase_detail", pk=dp.pk)

        item.unit_cost = cost
        item.save(update_fields=["unit_cost"])
        total += item.total_cost

    # The current model workflow is APPROVED -> PAID -> RECEIVED.
    # Do not write the removed PURCHASED/vendor_name/purchased_at fields.
    dp.total_cost = total
    dp.save(update_fields=["total_cost"])

    messages.success(request, "Direct purchase costs recorded.")
    return redirect("direct_purchase_detail", pk=dp.pk)


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_ingredient_requests(request):
    hotels = get_accessible_hotels(request.user)
    requests = (
        IngredientRestockRequest.objects
        .filter(requested_by__department__hotel__in=hotels)
        .exclude(status="APPROVED", direct_purchase__isnull=False)
        .prefetch_related("items__ingredient")
        .select_related("requested_by", "requested_by__department", "direct_purchase")
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
    all_requests = IngredientRestockRequest.objects.filter(requested_by__department__hotel__in=hotels)

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
        # Validate the request and all line items before persisting anything.
        # Invalid submissions must not leave empty/orphaned requests behind.
        req = IngredientRestockRequest(
            requested_by=request.user,
            note=request.POST.get("note", ""),
        )
        formset = IngredientRestockItemFormSet(
            request.POST,
            instance=req,
            prefix="items",
        )

        if formset.is_valid():
            req.save()
            formset.instance = req
            formset.save()
            messages.success(request, "Ingredient request submitted.")
            return redirect("kitchen_ingredients")

        request_form = IngredientRestockRequestForm(
            initial={"note": request.POST.get("note", "")}
        )
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


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT")
@transaction.atomic
def kitchen_ingredient_request_detail(request, pk):
    hotels = get_accessible_hotels(request.user)
    req = get_object_or_404(
        IngredientRestockRequest.objects.select_for_update().select_related(
            "requested_by", "requested_by__department", "requested_by__hotel",
        ).prefetch_related("items__ingredient").filter(
            Q(requested_by__hotel__in=hotels)
            | Q(requested_by__department__hotel__in=hotels)
        ),
        pk=pk,
    )

    is_manager = request.user.role in {"MANAGER", "ADMIN", "DIRECTOR"}
    is_kitchen = request.user.role == "KITCHEN"

    if is_kitchen and req.requested_by_id != request.user.id:
        raise PermissionDenied

    if request.method == "POST" and is_manager:
        if req.status != "PENDING":
            messages.warning(request, "This request is already processed.")
            return redirect(request.path)

        action = request.POST.get("action", "").strip()
        note = request.POST.get("manager_note", "").strip()

        if action == "reject":
            req.status = "REJECTED"
            req.note = note
            req.approved_by = request.user
            req.save(update_fields=["status", "note", "approved_by"])
            messages.info(request, "Ingredient request rejected.")
            return redirect("manager_ingredient_requests")

        if action != "approve":
            messages.error(request, "Choose a valid approval action.")
            return redirect(request.path)

        items = list(req.items.select_related("ingredient"))
        if not items:
            messages.error(request, "This request has no ingredient items.")
            return redirect(request.path)

        # All requested products must belong to the requester's hotel.
        requester_hotel_id = getattr(req.requested_by, "hotel_id", None)
        if not requester_hotel_id and req.requested_by and req.requested_by.department_id:
            requester_hotel_id = req.requested_by.department.hotel_id

        if not requester_hotel_id or any(
            item.ingredient.hotel_id != requester_hotel_id for item in items
        ):
            raise PermissionDenied("The request contains ingredients outside the requester's hotel.")

        if req.has_direct_items():
            if hasattr(req, "direct_purchase"):
                messages.error(request, "A direct purchase already exists for this request.")
                return redirect(request.path)

            supplier_id = request.POST.get("supplier", "").strip()
            if not supplier_id:
                messages.error(request, "Supplier is required for direct purchase.")
                return redirect(request.path)

            supplier = get_object_or_404(
                Supplier,
                pk=supplier_id,
                hotel_id=requester_hotel_id,
                hotel__in=hotels,
                is_active=True,
            )

            purchase_items = [item for item in items if item.source == "DIRECT"]
            if not purchase_items:
                messages.error(request, "No direct-purchase items were found.")
                return redirect(request.path)

            validated_costs = []
            for item in purchase_items:
                raw_cost = request.POST.get(f"cost_{item.pk}", "0").strip()
                try:
                    cost = Decimal(raw_cost)
                except (InvalidOperation, TypeError, ValueError):
                    messages.error(request, f"Enter a valid unit cost for {item.ingredient.name}.")
                    return redirect(request.path)

                if not cost.is_finite() or cost < 0:
                    messages.error(request, f"Unit cost for {item.ingredient.name} must be finite and non-negative.")
                    return redirect(request.path)

                validated_costs.append((item, cost))

            # Persist purchase and request state only after every line is valid.
            dp = DirectPurchase.objects.create(
                ingredient_request=req,
                supplier=supplier,
                requested_by=req.requested_by,
                approved_by=request.user,
                status="APPROVED",
                note=note,
                approved_at=timezone.now(),
            )
            total = Decimal("0.00")
            for item, cost in validated_costs:
                dpi = DirectPurchaseItem.objects.create(
                    purchase=dp,
                    product=item.ingredient,
                    quantity=item.quantity,
                    unit_cost=cost,
                )
                total += dpi.total_cost

            dp.total_cost = total
            dp.save(update_fields=["total_cost"])

        req.status = "APPROVED"
        req.note = note
        req.approved_by = request.user
        req.save(update_fields=["status", "note", "approved_by"])
        messages.success(request, "Ingredient request approved.")
        # Managers stay in the financial purchase workspace after approval.
        if req.has_direct_items() and hasattr(req, "direct_purchase"):
            return redirect("direct_purchase_detail", pk=req.direct_purchase.pk)
        return redirect("manager_ingredient_requests")

    return render(
        request,
        "kitchen/ingredients/request_detail.html",
        {
            "request_obj": req,
            "is_manager": is_manager,
            "is_kitchen": is_kitchen,
            "has_direct_items": req.has_direct_items(),
            "suppliers": Supplier.objects.filter(
                hotel__in=hotels,
                is_active=True,
            ).order_by("name"),
        },
    )

@role_required("KITCHEN")
@require_POST
def kitchen_confirm_direct_ingredients(request, pk):
    hotels = get_accessible_hotels(request.user)
    req = get_object_or_404(
        IngredientRestockRequest.objects.select_related(
            "requested_by", "requested_by__hotel", "requested_by__department",
        ).filter(
            Q(requested_by__hotel__in=hotels)
            | Q(requested_by__department__hotel__in=hotels)
        ),
        pk=pk,
        requested_by=request.user,
    )

    # The OneToOneField's related_name is "direct_purchase".
    direct_purchase = getattr(req, "direct_purchase", None)
    if direct_purchase is None:
        messages.error(request, "Direct purchase not created yet.")
        return redirect("kitchen_ingredient_requests")

    return redirect("kitchen_incoming_deliveries")


@role_required("ACCOUNTANT", "CHIEF_ACCOUNTANT", "DIRECTOR", "ADMIN")
@require_POST
@transaction.atomic
def direct_purchase_pay(request, pk):
    messages.info(request, "Supplier payments are now recorded through Accounting → Supplier & Vendor Payments.")
    return redirect("accounting:supplier_payments")

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


@role_required("KITCHEN")
def kitchen_incoming_deliveries(request):
    """Kitchen-only operational view of purchases awaiting physical receipt."""
    purchases = (
        DirectPurchase.objects
        .filter(
            requested_by=request.user,
            status__in=("PAID", "PARTIALLY_RECEIVED"),
            supplier__hotel__in=get_accessible_hotels(request.user),
        )
        .select_related("supplier", "ingredient_request")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )
    return render(
        request,
        "kitchen/incoming_deliveries.html",
        {"purchases": purchases},
    )


@role_required("KITCHEN")
def urgent_purchase_create(request):
    """Record an already-made urgent purchase for management reconciliation."""
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can record urgent purchases.")

    hotels = get_accessible_hotels(request.user)
    suppliers = Supplier.objects.filter(hotel__in=hotels, is_active=True).order_by("name")
    ingredients = Product.objects.filter(hotel__in=hotels, product_type="RAW", is_active=True).order_by("name") if hasattr(Product, "is_active") else Product.objects.filter(hotel__in=hotels, product_type="RAW").order_by("name")

    if request.method == "POST":
        supplier_id = request.POST.get("supplier", "")
        purchase_date = request.POST.get("purchase_date") or timezone.localdate().isoformat()
        payment_source = request.POST.get("payment_source", "")
        reason = request.POST.get("urgent_reason", "").strip()
        receipt_reference = request.POST.get("receipt_reference", "").strip()
        note = request.POST.get("note", "").strip()
        goods_received = request.POST.get("goods_already_received") == "on"
        try:
            supplier = suppliers.get(pk=supplier_id)
        except (Supplier.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Select a valid supplier for this hotel.")
            return redirect("urgent_purchase_create")
        if payment_source not in {"HOTEL_FUNDS", "STAFF_FUNDS", "UNPAID"} or not reason:
            messages.error(request, "Choose a payment source and explain why the purchase was urgent.")
            return redirect("urgent_purchase_create")

        rows = []
        total = Decimal("0.00")
        index = 0
        while f"product_{index}" in request.POST:
            product_id = request.POST.get(f"product_{index}")
            quantity_raw = request.POST.get(f"quantity_{index}", "")
            cost_raw = request.POST.get(f"unit_cost_{index}", "")
            try:
                product = ingredients.get(pk=product_id)
                quantity = Decimal(quantity_raw)
                unit_cost = Decimal(cost_raw)
            except (Product.DoesNotExist, ValueError, TypeError, InvalidOperation):
                messages.error(request, f"Check ingredient, quantity, and unit cost on line {index + 1}.")
                return redirect("urgent_purchase_create")
            if not quantity.is_finite() or quantity <= 0 or not unit_cost.is_finite() or unit_cost < 0:
                messages.error(request, f"Line {index + 1} must have a positive quantity and a valid non-negative cost.")
                return redirect("urgent_purchase_create")
            rows.append((product, quantity, unit_cost))
            total += quantity * unit_cost
            index += 1

        if not rows:
            messages.error(request, "Add at least one ingredient to the urgent purchase.")
            return redirect("urgent_purchase_create")

        from datetime import date
        try:
            parsed_date = date.fromisoformat(purchase_date)
        except ValueError:
            messages.error(request, "Enter a valid purchase date.")
            return redirect("urgent_purchase_create")

        with transaction.atomic():
            purchase = DirectPurchase.objects.create(
                ingredient_request=None,
                supplier=supplier,
                requested_by=request.user,
                total_cost=total.quantize(Decimal("0.01")),
                status="RECONCILIATION_PENDING",
                note=note,
                purchase_date=parsed_date,
                payment_source=payment_source,
                receipt_reference=receipt_reference,
                urgent_reason=reason,
                urgent_purchase=True,
                goods_already_received=goods_received,
            )
            DirectPurchaseItem.objects.bulk_create([
                DirectPurchaseItem(purchase=purchase, product=product, quantity=quantity, unit_cost=cost)
                for product, quantity, cost in rows
            ])
        messages.success(request, f"Urgent purchase DP-{purchase.pk} submitted for reconciliation. No stock or accounting entries have been posted yet.")
        return redirect("kitchen_ingredient_requests")

    return render(request, "kitchen/direct_purchase/urgent_create.html", {
        "suppliers": suppliers,
        "ingredients": ingredients,
        "today": timezone.localdate().isoformat(),
    })


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
@require_POST
@transaction.atomic
def urgent_purchase_reconcile(request, pk):
    """Approve or reject an urgent purchase; this action does not post payment or accounting."""
    purchase = get_object_or_404(
        DirectPurchase.objects.select_for_update().select_related("supplier"),
        pk=pk,
        urgent_purchase=True,
        supplier__hotel__in=get_accessible_hotels(request.user),
    )
    if purchase.status != "RECONCILIATION_PENDING":
        messages.error(request, "Only purchases awaiting reconciliation can be reviewed.")
        return redirect("urgent_purchase_review")

    decision = request.POST.get("decision", "").strip().upper()
    note = request.POST.get("reconciliation_note", "").strip()
    if decision not in {"APPROVE", "REJECT"}:
        messages.error(request, "Choose approve or reject.")
        return redirect("urgent_purchase_review")
    if not note:
        messages.error(request, "A reconciliation note is required for the audit trail.")
        return redirect("urgent_purchase_review")
    if decision == "REJECT":
        purchase.status = "REJECTED"
        message = f"Urgent purchase DP-{purchase.pk} rejected."
    else:
        # Approval authorizes receipt processing only. Supplier payment,
        # reimbursement and journal posting remain separate controls.
        purchase.status = "APPROVED"
        message = f"Urgent purchase DP-{purchase.pk} approved for controlled receiving."
    purchase.reconciliation_note = note
    purchase.reconciled_by = request.user
    purchase.reconciled_at = timezone.now()
    purchase.save(update_fields=[
        "status", "reconciliation_note", "reconciled_by", "reconciled_at"
    ])
    messages.success(request, message)
    return redirect("urgent_purchase_review")


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def urgent_purchase_review(request):
    purchases = DirectPurchase.objects.filter(
        urgent_purchase=True,
        supplier__hotel__in=get_accessible_hotels(request.user),
    ).select_related("supplier", "requested_by").prefetch_related("items__product").order_by("-created_at")
    return render(request, "kitchen/direct_purchase/urgent_review.html", {"purchases": purchases})


@role_required("MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def direct_purchase_list(request):
    qs = DirectPurchase.objects.select_related(
        "supplier", "requested_by", "supplier__hotel"
    ).filter(
        supplier__hotel__in=get_accessible_hotels(request.user)
    ).order_by("-created_at")

    if request.user.role == "KITCHEN":
        qs = qs.filter(requested_by=request.user)
    
    from django.db.models import Sum, F, DecimalField, ExpressionWrapper

    purchases = (
        qs
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
        {"purchases": purchases}
    )

@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def recipe_ingredients_api(request, recipe_id):
    hotels = get_accessible_hotels(request.user)
    recipe = get_object_or_404(
        Recipe.objects.select_related("product"),
        id=recipe_id,
        product__hotel__in=hotels,
    )
    try:
        qty = Decimal(request.GET.get("qty", "1"))
    except (ArithmeticError, ValueError):
        return JsonResponse({"error": "Invalid quantity."}, status=400)
    if not qty.is_finite() or qty <= 0 or qty > Decimal("10000"):
        return JsonResponse({"error": "Quantity must be positive and within the allowed range."}, status=400)

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

    hotels = get_accessible_hotels(request.user)
    tickets = (
        KitchenTicket.objects
        .select_related("order", "order__department", "room")
        .prefetch_related("items__menu_item")
        .filter(
            order__department__hotel__in=hotels,
            status__in=["NEW", "PREPARING", "READY"],
        )
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
@require_POST
@transaction.atomic
def direct_purchase_receive(request, pk):
    dp = get_object_or_404(
        DirectPurchase.objects.select_for_update().select_related(
            "supplier", "requested_by", "requested_by__department",
            "ingredient_request",
        ),
        pk=pk,
        status__in=["APPROVED", "PAID", "PARTIALLY_RECEIVED"],
        supplier__hotel__in=get_accessible_hotels(request.user),
    )

    if dp.requested_by != request.user:
        raise PermissionDenied

    kitchen = request.user.department
    if (
        not kitchen
        or kitchen.department_type != "KITCHEN"
        or kitchen.hotel_id != dp.supplier.hotel_id
    ):
        raise PermissionDenied

    items = list(dp.items.select_for_update().select_related("product"))
    if not items:
        messages.error(request, "This direct purchase has no items to receive.")
        return redirect("kitchen_incoming_deliveries")

    # Validate every submitted quantity before changing any stock.
    receipt_quantities = {}
    total_to_receive = Decimal("0.000")
    for item in items:
        if item.quantity <= 0:
            raise ValidationError(
                f"Ordered quantity for {item.product.name} must be greater than zero."
            )
        if not item.unit_cost.is_finite() or item.unit_cost < 0:
            raise ValidationError(
                f"Unit cost for {item.product.name} cannot be negative."
            )
        if item.product.hotel_id != kitchen.hotel_id:
            raise PermissionDenied

        raw_quantity = request.POST.get(f"received_{item.pk}", "0").strip()
        try:
            received_now = Decimal(raw_quantity)
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, f"Enter a valid received quantity for {item.product.name}.")
            return redirect("kitchen_incoming_deliveries")

        if not received_now.is_finite() or received_now < 0:
            messages.error(request, f"Received quantity for {item.product.name} must be zero or greater.")
            return redirect("kitchen_incoming_deliveries")

        if received_now > item.outstanding_quantity:
            messages.error(
                request,
                f"Received quantity for {item.product.name} exceeds the outstanding "
                f"quantity ({item.outstanding_quantity}).",
            )
            return redirect("kitchen_incoming_deliveries")

        receipt_quantities[item.pk] = received_now
        total_to_receive += received_now

    if total_to_receive <= 0:
        messages.error(request, "Enter a positive quantity for at least one item to receive.")
        return redirect("kitchen_incoming_deliveries")

    for item in items:
        received_now = receipt_quantities[item.pk]
        if received_now == 0:
            continue

        stock, _ = Stock.objects.select_for_update().get_or_create(
            product=item.product,
            department=kitchen,
            defaults={"quantity": 0},
        )
        stock.quantity += received_now
        stock.save(update_fields=["quantity"])

        new_total_received = item.quantity_received + received_now
        StockMovement.objects.create(
            product=item.product,
            to_department=kitchen,
            quantity=received_now,
            movement_type="IN",
            created_by=request.user,
            reference=f"DP-{dp.id}-ITEM-{item.pk}-TOTAL-{new_total_received}",
        )

        # Preserve the purchase's unit cost in a separate FIFO layer for
        # each partial delivery. Quantities are already in the base unit.
        StockCostLayer.objects.create(
            product=item.product,
            department=kitchen,
            purchase_item=None,
            quantity_received=received_now,
            quantity_remaining=received_now,
            unit_cost=item.unit_cost,
            is_opening_balance=False,
        )

        item.quantity_received = new_total_received
        item.save(update_fields=["quantity_received"])

    all_received = all(
        item.quantity_received >= item.quantity
        for item in items
    )
    dp.status = "RECEIVED" if all_received else "PARTIALLY_RECEIVED"
    update_fields = ["status"]
    if all_received:
        dp.received_at = timezone.now()
        update_fields.append("received_at")

        # Recognize staff-funded urgent purchases only after all goods are verified.
        if dp.urgent_purchase and dp.payment_source == "STAFF_FUNDS":
            from accounting.models import JournalEntry, StaffReimbursement
            from accounting.services.journal import get_system_account, record_transaction

            hotel = dp.supplier.hotel
            reimbursement = StaffReimbursement.objects.select_for_update().filter(
                direct_purchase=dp, hotel=hotel
            ).first()
            if reimbursement is None and dp.requested_by_id:
                reimbursement = StaffReimbursement.objects.create(
                    hotel=hotel, direct_purchase=dp, claimant=dp.requested_by,
                    amount=dp.total_cost, status="PENDING",
                )
            if reimbursement is not None:
                amount = Decimal(str(dp.total_cost or "0.00"))
                if amount <= 0:
                    raise ValidationError("The verified purchase total must be positive before accounting recognition.")
                reference = f"STAFF-REIMB-{reimbursement.pk}-OBL"
                liability = JournalEntry.objects.filter(
                    hotel=hotel, reference=reference, entry_type="PURCHASE"
                ).first()
                if liability is None:
                    inventory_account = get_system_account(hotel, "inventory_asset")
                    payable_account = get_system_account(hotel, "accounts_payable")
                    liability = record_transaction(
                        debit_account=inventory_account, credit_account=payable_account,
                        amount=amount, description=f"Verified staff-funded urgent purchase DP-{dp.pk}",
                        hotel=hotel, created_by=request.user, entry_type="PURCHASE", reference=reference,
                    )
                reimbursement.liability_journal = liability
                reimbursement.save(update_fields=["liability_journal"])
    else:
        dp.received_at = None
        update_fields.append("received_at")
    dp.save(update_fields=update_fields)

    req = dp.ingredient_request
    if req:
        req.update_issue_status()

    if all_received:
        messages.success(request, f"Direct Purchase #{dp.id} fully received.")
    else:
        outstanding = sum(
            (item.outstanding_quantity for item in items),
            Decimal("0.000"),
        )
        messages.success(
            request,
            f"Partial delivery recorded for Direct Purchase #{dp.id}. "
            f"{outstanding} unit(s) remain outstanding.",
        )
    return redirect("kitchen_incoming_deliveries")


@role_required("MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")

def direct_purchase_detail(request, pk):
    from accounting.models import SupplierPayment
    from decimal import Decimal

    dp = get_object_or_404(
        DirectPurchase.objects
        .select_related("supplier", "supplier__hotel", "ingredient_request", "requested_by", "approved_by", "paid_by")
        .prefetch_related("items__product"),
        pk=pk,
        supplier__hotel__in=get_accessible_hotels(request.user),
    )

    if request.user.role == "KITCHEN" and dp.requested_by != request.user:
        raise PermissionDenied

    payments = SupplierPayment.objects.filter(
        hotel_id=dp.supplier.hotel_id,
        source_type="DIRECT",
        source_id=dp.pk,
    ).select_related("payment_account", "created_by", "journal_entry").order_by("-payment_date", "-pk")
    paid_to_date = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    purchase_total = Decimal(str(dp.total_cost or "0.00"))
    payment_outstanding = max(Decimal("0.00"), purchase_total - paid_to_date)
    can_manage_payment = request.user.role in {"ACCOUNTANT", "CHIEF_ACCOUNTANT", "DIRECTOR", "ADMIN"}

    return render(
        request,
        "kitchen/direct_purchase/detail.html",
        {
            "dp": dp,
            "payments": payments,
            "paid_to_date": paid_to_date,
            "payment_outstanding": payment_outstanding,
            "payment_complete": purchase_total > 0 and payment_outstanding == 0,
            "can_manage_payment": can_manage_payment,
            "payment_url": f"/accounting/supplier-payments/?hotel={dp.supplier.hotel_id}",
            "can_receive": (
                request.user.role in {"KITCHEN", "MANAGER", "ADMIN", "DIRECTOR"}
                and dp.status in ("PAID", "PARTIALLY_RECEIVED")
            ),
        },
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
@require_POST
def kitchen_ticket_start(request, ticket_id):

    ticket = get_object_or_404(
        KitchenTicket.objects.select_related("order__department"),
        id=ticket_id,
        order__department__hotel__in=get_accessible_hotels(request.user),
    )

    if ticket.status != "NEW":
        messages.error(request, "Ticket already started.")
        return redirect("kitchen_dashboard")

    ticket.status = "PREPARING"
    ticket.eta_minutes = request.POST.get("eta_minutes")

    ticket.save()

    return redirect("kitchen_dashboard")



@role_required("KITCHEN")
@require_POST
def kitchen_ticket_ready(request, ticket_id):

    ticket = get_object_or_404(
        KitchenTicket.objects.select_related("order__department"),
        id=ticket_id,
        order__department__hotel__in=get_accessible_hotels(request.user),
    )

    ticket.status = "READY"
    ticket.save(update_fields=["status"])

    return redirect("kitchen_dashboard")

@role_required("KITCHEN")
@require_POST
@transaction.atomic
def kitchen_ticket_served(request, ticket_id):

    ticket = get_object_or_404(
        KitchenTicket.objects.select_related("order__department"),
        id=ticket_id,
        order__department__hotel__in=get_accessible_hotels(request.user),
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