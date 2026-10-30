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
from datetime import date
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
    StockMovementCostAllocation,
    StockTransfer,
    stock_out,
)

from .models import (
    Recipe,
    RecipeItem,
    RecipeVersion,
    RecipeVersionItem,
    ProductionBatch,
    IngredientRestockRequest,
    IngredientRestockItem,
    DirectPurchase,
    DirectPurchaseItem,
    KitchenTicket,
    KitchenTicketItem,
    KitchenSpoilageRecord,
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
    """Kitchen-only queue for eligible regular and reconciled urgent purchases."""
    kitchen = request.user.department
    if not kitchen or kitchen.department_type != "KITCHEN":
        raise PermissionDenied("You must be assigned to a Kitchen department.")

    purchases = DirectPurchase.objects.filter(
        supplier__hotel__in=get_accessible_hotels(request.user),
        requested_by__department=kitchen,
    ).filter(
        Q(
            urgent_purchase=False,
            status__in=("APPROVED", "PAID", "PARTIALLY_RECEIVED"),
        )
        | Q(urgent_purchase=True, status="APPROVED")
    )

    # Kitchen staff see their own purchases. The Head Chef can coordinate
    # receiving for all eligible purchases requested by this Kitchen.
    if not request.user.is_department_head:
        purchases = purchases.filter(requested_by=request.user)

    purchases = (
        purchases.select_related("supplier", "ingredient_request", "requested_by")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )
    return render(
        request,
        "kitchen/incoming_deliveries.html",
        {
            "purchases": purchases,
            "can_view_purchase_orders": request.user.is_department_head,
        },
    )


@role_required("KITCHEN")
def urgent_purchase_history(request):
    """History of urgent purchases submitted by the current kitchen head."""
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can view this register.")
    purchases = DirectPurchase.objects.filter(
        urgent_purchase=True, requested_by=request.user,
        supplier__hotel__in=get_accessible_hotels(request.user),
    ).select_related("supplier", "reconciled_by").prefetch_related("items__product").order_by("-created_at")
    return render(request, "kitchen/direct_purchase/urgent_history.html", {"purchases": purchases})


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
        return redirect("urgent_purchase_history")

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

    # Production must be based on the approved immutable version, not
    # the editable working recipe. This keeps the form and the batch aligned.
    version = recipe.active_version
    if not version or version.status != "APPROVED":
        return JsonResponse(
            {"error": "This food has no approved recipe version. Ask the Kitchen Head to approve one."},
            status=409,
        )

    data = [
        {
            "ingredient_id": item.ingredient.id,
            "ingredient": item.ingredient.name,
            "expected_quantity": float(item.quantity) * float(qty),
            "per_unit_quantity": float(item.quantity),
            "control_mode": item.control_mode,
            "unit": item.unit or item.ingredient.base_unit,
            "tolerance_percent": float(item.tolerance_percent),
        }
        for item in version.items.select_related("ingredient")
    ]

    return JsonResponse(data, safe=False)


@role_required("KITCHEN")
def kitchen_quick_production(request):
    IngredientFormSet = formset_factory(IngredientUsageForm, extra=0)

    if request.method == "POST":
        batch_form = ProductionBatchForm(request.POST)
        formset = IngredientFormSet(request.POST, prefix="ingredients")

        if batch_form.is_valid() and formset.is_valid():
            recipe = batch_form.cleaned_data["recipe"]
            qty = batch_form.cleaned_data["quantity_produced"]
            version = recipe.active_version

            if not version or version.status != "APPROVED" or not version.items.exists():
                messages.error(request, f"{recipe.name} needs an approved recipe version with ingredients.")
                return redirect("kitchen_quick_production")

            # Do not trust ingredient IDs supplied by the browser. They must
            # exactly match the approved snapshot for this recipe.
            expected_ids = set(version.items.values_list("ingredient_id", flat=True))
            submitted_ids = [f.cleaned_data["ingredient_id"] for f in formset]
            if len(submitted_ids) != len(set(submitted_ids)) or set(submitted_ids) != expected_ids:
                messages.error(request, "Ingredient list changed or is incomplete. Reload the recipe and try again.")
                return redirect("kitchen_quick_production")

            actual_quantities = {
                str(f.cleaned_data["ingredient_id"]): {
                    "quantity": f.cleaned_data["actual_quantity"],
                    "reason": f.cleaned_data.get("variance_reason", ""),
                }
                for f in formset
            }
            strict_variances = []
            for item in version.items.select_related("ingredient"):
                entry = actual_quantities[str(item.ingredient_id)]
                actual = Decimal(str(entry["quantity"]))
                expected = Decimal(item.quantity) * Decimal(qty)
                if item.control_mode == "STRICT" and actual != expected:
                    reason = str(entry.get("reason", "")).strip()
                    if not reason:
                        messages.error(request, f"Provide a reason for the STRICT variance on {item.ingredient}.")
                        return redirect("kitchen_quick_production")
                    strict_variances.append(item.ingredient.name)

            batch = ProductionBatch.objects.create(
                recipe=recipe,
                recipe_version=version,
                quantity_produced=qty,
                produced_by=request.user,
            )
            try:
                batch.execute(actual_quantities=actual_quantities)
                messages.success(request, f"{qty} portions of {recipe.name} produced successfully.")
            except ValidationError as exc:
                if any("STRICT recipe requires exactly" in message for message in exc.messages) and strict_variances:
                    batch.override_status = "PENDING"
                    batch.pending_actual_quantities = {
                        ingredient_id: {
                            "quantity": str(entry["quantity"]),
                            "reason": str(entry.get("reason", "")),
                        }
                        for ingredient_id, entry in actual_quantities.items()
                    }
                    batch.override_requested_by = request.user
                    batch.override_reason = "; ".join(
                        f"{name}: {actual_quantities[str(item.ingredient_id)].get('reason', '').strip()}"
                        for item in version.items.select_related("ingredient")
                        if item.ingredient.name in strict_variances
                    )
                    batch.save(update_fields=[
                        "override_status", "pending_actual_quantities",
                        "override_requested_by", "override_reason",
                    ])
                    messages.warning(
                        request,
                        f"Production #{batch.pk} is awaiting manager approval for STRICT recipe variance. "
                        "No stock has been deducted yet."
                    )
                else:
                    messages.error(request, "Production failed: " + ", ".join(exc.messages))
                    batch.delete()
            return redirect("kitchen_quick_production")
        messages.error(request, "Invalid production data.")
    else:
        batch_form = ProductionBatchForm()
        formset = IngredientFormSet(prefix="ingredients")

    return render(request, "kitchen/quick_production.html", {
        "batch_form": batch_form,
        "formset": formset,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
@require_POST
@transaction.atomic
def production_override_approve(request, pk):
    hotels = get_accessible_hotels(request.user)
    batch = get_object_or_404(
        ProductionBatch.objects.select_for_update().select_related(
            "recipe__product", "recipe_version", "produced_by",
            "produced_by__department__hotel",
        ),
        pk=pk,
        override_status="PENDING",
        is_executed=False,
        recipe__product__hotel__in=hotels,
    )

    if batch.override_requested_by_id == request.user.pk or batch.produced_by_id == request.user.pk:
        messages.error(request, "You cannot approve your own production variance request.")
        return redirect("kitchen_production_history")

    if not batch.pending_actual_quantities:
        messages.error(request, "This request has no saved ingredient quantities.")
        return redirect("kitchen_production_history")

    batch.override_status = "APPROVED"
    batch.override_approved_by = request.user
    batch.override_approved_at = timezone.now()
    batch.save(update_fields=["override_status", "override_approved_by", "override_approved_at"])

    try:
        batch.execute(
            actual_quantities=batch.pending_actual_quantities,
            allow_strict_override=True,
        )
    except ValidationError as exc:
        # Atomic transaction ensures the approval and any stock/accounting
        # changes are rolled back together if execution cannot complete.
        batch.override_status = "PENDING"
        batch.override_approved_by = None
        batch.override_approved_at = None
        batch.save(update_fields=["override_status", "override_approved_by", "override_approved_at"])
        messages.error(request, "Approval could not be executed: " + ", ".join(exc.messages))
        return redirect("kitchen_production_history")

    messages.success(request, f"Production #{batch.pk} approved and completed.")
    return redirect("kitchen_production_detail", pk=batch.pk)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
@require_POST
@transaction.atomic
def production_override_reject(request, pk):
    hotels = get_accessible_hotels(request.user)
    batch = get_object_or_404(
        ProductionBatch.objects.select_for_update().select_related(
            "recipe__product", "produced_by"
        ),
        pk=pk,
        override_status="PENDING",
        is_executed=False,
        recipe__product__hotel__in=hotels,
    )
    if batch.override_requested_by_id == request.user.pk or batch.produced_by_id == request.user.pk:
        messages.error(request, "You cannot reject your own production variance request.")
        return redirect("kitchen_production_history")
    batch.override_status = "REJECTED"
    batch.override_rejected_by = request.user
    batch.override_rejected_at = timezone.now()
    batch.save(update_fields=["override_status", "override_rejected_by", "override_rejected_at"])
    messages.warning(request, f"Production #{batch.pk} variance request rejected. No stock was deducted.")
    return redirect("kitchen_production_history")



@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def kitchen_spoilage(request):
    """Record kitchen stock spoilage immediately and retain it for manager review."""
    kitchen = request.user.department
    is_kitchen_user = request.user.role == "KITCHEN"
    hotels = get_accessible_hotels(request.user)
    if is_kitchen_user and (not kitchen or kitchen.department_type != "KITCHEN"):
        raise PermissionDenied("You must be assigned to a Kitchen department.")
    if request.method == "POST" and not is_kitchen_user:
        raise PermissionDenied("Only Kitchen staff can record spoilage.")

    products = Product.objects.filter(
        hotel_id=kitchen.hotel_id if is_kitchen_user else None,
        product_type__in=("RAW", "FOOD"),
        is_active=True,
    ).order_by("name") if is_kitchen_user else Product.objects.filter(
        hotel__in=hotels,
        product_type__in=("RAW", "FOOD"),
        is_active=True,
    ).order_by("name")
    records = KitchenSpoilageRecord.objects.filter(
        department=kitchen,
    ).select_related("product", "recorded_by", "reviewed_by", "stock_movement") if is_kitchen_user else KitchenSpoilageRecord.objects.filter(
        department__hotel__in=hotels,
    ).select_related("product", "department", "recorded_by", "reviewed_by", "stock_movement")

    if request.method == "POST":
        try:
            product = products.get(pk=request.POST.get("product", ""))
        except (Product.DoesNotExist, ValueError, TypeError):
            messages.error(request, "Select a valid Kitchen ingredient or prepared food.")
            return redirect("kitchen_spoilage")

        try:
            quantity = Decimal(request.POST.get("quantity", "").strip())
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter a valid spoilage quantity.")
            return redirect("kitchen_spoilage")
        if not quantity.is_finite() or quantity <= 0:
            messages.error(request, "Spoilage quantity must be greater than zero.")
            return redirect("kitchen_spoilage")

        reason_code = request.POST.get("reason_code", "").strip()
        valid_reasons = {value for value, _label in KitchenSpoilageRecord.REASON_CHOICES}
        if reason_code not in valid_reasons:
            messages.error(request, "Choose a valid spoilage reason.")
            return redirect("kitchen_spoilage")
        notes = request.POST.get("notes", "").strip()

        try:
            with transaction.atomic():
                record = KitchenSpoilageRecord.objects.create(
                    product=product,
                    department=kitchen,
                    quantity=quantity,
                    reason_code=reason_code,
                    notes=notes,
                    recorded_by=request.user,
                )
                result = stock_out(
                    product=product,
                    department=kitchen,
                    quantity=quantity,
                    user=request.user,
                    reason=f"Kitchen spoilage #{record.pk}",
                    reference=f"SPOILAGE-{record.pk}",
                    allow_negative=False,
                    allow_uncosted_shortage=True,
                )
                uncosted_quantity = Decimal(
                    str(result.get("uncosted_quantity", "0"))
                )
                if uncosted_quantity > 0:
                    raise ValidationError(
                        f"Cannot record spoilage: {uncosted_quantity} {product.base_unit} "
                        "has no FIFO cost-layer coverage. Reconcile inventory costing first; "
                        "no stock or spoilage record was changed."
                    )
                cost = Decimal(str(result["total_cost"] or "0.00")).quantize(Decimal("0.01"))
                record.stock_movement = result["movement"]
                record.cost_amount = cost
                record.save(update_fields=["stock_movement", "cost_amount"])

                if cost > 0:
                    from accounting.services.journal import get_system_account, post_journal_entry
                    hotel = kitchen.hotel
                    expense = get_system_account(hotel, "spoilage_expense")
                    inventory_key = "finished_goods_inventory" if product.product_type == "FOOD" else "inventory_asset"
                    inventory_account = get_system_account(hotel, inventory_key)
                    post_journal_entry(
                        hotel=hotel,
                        description=f"Kitchen spoilage #{record.pk} - {product.name}",
                        created_by=request.user,
                        entry_type="NORMAL",
                        reference=f"SPOILAGE-{record.pk}",
                        lines=[
                            {"account": expense, "debit": cost},
                            {"account": inventory_account, "credit": cost},
                        ],
                    )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("kitchen_spoilage")

        messages.success(
            request,
            f"Logged {quantity} {product.base_unit} of {product.name} as spoilage. Stock was deducted and the record is pending management review.",
        )
        return redirect("kitchen_spoilage")

    return render(
        request,
        "kitchen/spoilage.html",
        {
            "products": products,
            "records": records,
            "reason_choices": KitchenSpoilageRecord.REASON_CHOICES,
            "can_review": (
                request.user.role in {"MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR"}
                or (
                    is_kitchen_user
                    and request.user.is_department_head
                    and kitchen is not None
                    and kitchen.department_type == "KITCHEN"
                )
            ),
        },
    )


@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
@require_POST
@transaction.atomic
def kitchen_spoilage_review(request, pk):
    hotels = get_accessible_hotels(request.user)
    record = get_object_or_404(
        KitchenSpoilageRecord.objects.select_for_update().select_related("department"),
        pk=pk,
        department__hotel__in=hotels,
    )
    if request.user.role == "KITCHEN":
        kitchen = request.user.department
        if (
            not request.user.is_department_head
            or not kitchen
            or kitchen.department_type != "KITCHEN"
            or record.department_id != kitchen.pk
        ):
            raise PermissionDenied(
                "Only your Kitchen Head can review spoilage records for this kitchen."
            )
    if record.review_status == "REVIEWED":
        messages.info(request, "This spoilage record has already been reviewed.")
        return redirect("kitchen_spoilage")
    record.review_status = "REVIEWED"
    record.reviewed_by = request.user
    record.reviewed_at = timezone.now()
    record.review_note = request.POST.get("review_note", "").strip()
    record.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "review_note"])
    messages.success(request, f"Spoilage record #{record.pk} marked as reviewed.")
    return redirect("kitchen_spoilage")


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def kitchen_management_report(request):
    """Management-only overview of Kitchen sales, production, waste and fulfillment."""
    from restaurant.models import POSOrder, POSOrderItem
    from django.db.models import Sum

    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied("You do not have access to Kitchen management reports.")

    today = timezone.localdate()
    date_from_raw = request.GET.get("from", "").strip()
    date_to_raw = request.GET.get("to", "").strip()
    try:
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else today.replace(day=1)
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else today
    except ValueError:
        date_to = today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel.isdigit():
        scoped_hotels = hotels.filter(pk=int(selected_hotel))
        if not scoped_hotels.exists():
            raise PermissionDenied("You do not have access to that hotel.")
    else:
        scoped_hotels = hotels
        selected_hotel = ""

    sales_items = POSOrderItem.objects.filter(
        order__department__hotel__in=scoped_hotels,
        order__created_at__date__gte=date_from,
        order__created_at__date__lte=date_to,
        order__status__in=("CHARGED", "PAID"),
        order__is_refunded=False,
        menu_item__product__product_type="FOOD",
    ).select_related("order", "menu_item", "menu_item__product", "order__department__hotel")

    total_sales = Decimal("0.00")
    sales_cogs = Decimal("0.00")
    total_sold = 0
    for item in sales_items:
        total_sold += item.quantity
        total_sales += item.price * item.quantity
        sales_cogs += (item.cost_at_sale or Decimal("0")) * item.quantity

    # Keep the overview chart readable even when users select a long period:
    # show the latest 14 calendar days within the selected range.
    trend_end = date_to
    trend_start = max(date_from, trend_end - timezone.timedelta(days=13))
    trend_days = []
    cursor_day = trend_start
    while cursor_day <= trend_end:
        trend_days.append(cursor_day)
        cursor_day += timezone.timedelta(days=1)
    daily = {
        day: {"sales": Decimal("0.00"), "production": 0, "spoilage": Decimal("0.00")}
        for day in trend_days
    }
    for item in sales_items:
        day = item.order.created_at.date()
        if day in daily:
            daily[day]["sales"] += item.price * item.quantity

    batches = ProductionBatch.objects.filter(
        produced_by__department__hotel__in=scoped_hotels,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
        is_executed=True,
    ).select_related("recipe", "produced_by", "produced_by__department__hotel")
    # Batch-level traceability: follow the finished-food FIFO layer created
    # by each production batch through later POS sales and normal spoilage.
    batch_profitability_rows = []
    spoilage_movement_ids = set(
        KitchenSpoilageRecord.objects.filter(
            department__hotel__in=scoped_hotels,
            stock_movement__isnull=False,
        ).values_list("stock_movement_id", flat=True)
    )
    for batch in batches:
        product = batch.recipe.product
        source_ref = f"PROD-{batch.pk}"
        layers = StockCostLayer.objects.filter(
            source_reference=source_ref,
            product=product,
        )
        layer_ids = list(layers.values_list("pk", flat=True))
        layer_allocations = list(
            StockMovementCostAllocation.objects
            .filter(cost_layer_id__in=layer_ids)
            .select_related("movement", "cost_layer")
        ) if layer_ids else []

        sold_qty = Decimal("0.000")
        sold_cogs = Decimal("0.00")
        spoiled_qty = Decimal("0.000")
        spoiled_cost = Decimal("0.00")
        sales_by_order = {}

        for allocation in layer_allocations:
            movement = allocation.movement
            if movement.movement_type != "OUT":
                continue
            if movement.reference.startswith("POS-"):
                try:
                    order_id = int(movement.reference[4:])
                except ValueError:
                    continue
                sales_by_order.setdefault(order_id, []).append(allocation)
            elif movement.pk in spoilage_movement_ids:
                spoiled_qty += allocation.quantity
                spoiled_cost += allocation.total_cost

        if sales_by_order:
            valid_orders = {
                order.pk: order
                for order in POSOrder.objects.filter(
                    pk__in=sales_by_order,
                    department__hotel__in=scoped_hotels,
                    status__in=("CHARGED", "PAID"),
                    is_refunded=False,
                )
            }
            for order_id, allocations in sales_by_order.items():
                order = valid_orders.get(order_id)
                if not order:
                    continue
                allocated_qty = sum(
                    (allocation.quantity for allocation in allocations),
                    Decimal("0.000"),
                )
                if allocated_qty <= 0:
                    continue
                # MenuItem is unique per product, so an order has one line
                # per product. Allocate that line's revenue in proportion
                # to the FIFO quantity sourced from this production batch.
                line = POSOrderItem.objects.filter(
                    order_id=order_id,
                    menu_item__product=product,
                ).first()
                if not line:
                    continue
                sold_qty += allocated_qty
                sold_cogs += sum(
                    (allocation.total_cost for allocation in allocations),
                    Decimal("0.00"),
                )
                total_product_allocated = StockMovementCostAllocation.objects.filter(
                    movement__reference=f"POS-{order_id}",
                    movement__product=product,
                    movement__movement_type="OUT",
                ).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
                if total_product_allocated > 0:
                    batch_profit_revenue = (
                        line.price * line.quantity * allocated_qty
                        / total_product_allocated
                    )
                else:
                    batch_profit_revenue = Decimal("0.00")
                sales_by_order[order_id] = (allocations, batch_profit_revenue)

        sales_revenue = sum(
            (value[1] for value in sales_by_order.values() if isinstance(value, tuple)),
            Decimal("0.00"),
        )
        remaining_qty = sum(
            (layer.quantity_remaining for layer in layers),
            Decimal("0.000"),
        )
        remaining_value = sum(
            (
                layer.quantity_remaining * layer.unit_cost
                for layer in layers
            ),
            Decimal("0.00"),
        )
        actual_production_cost = batch.total_cost()
        # A batch without a confidently identified finished-goods FIFO layer
        # cannot be reconciled from allocations. This is common in legacy
        # records created before production provenance was recorded; do not
        # present the full batch quantity as a stock discrepancy.
        traceability_status = "TRACEABLE" if layers.exists() else "UNATTRIBUTED"
        reconciliation_gap = (
            Decimal(batch.quantity_produced)
            - sold_qty - spoiled_qty - remaining_qty
            if traceability_status == "TRACEABLE"
            else None
        )
        batch_profitability_rows.append({
            "batch_id": batch.pk,
            "food_item": product.name,
            "produced_at": batch.created_at,
            "quantity_produced": batch.quantity_produced,
            "production_cost": actual_production_cost,
            "quantity_sold": sold_qty,
            "sales_revenue": sales_revenue,
            "sales_cogs": sold_cogs,
            "gross_profit": sales_revenue - sold_cogs,
            "quantity_spoiled": spoiled_qty,
            "spoilage_cost": spoiled_cost,
            "remaining_quantity": remaining_qty,
            "remaining_value": remaining_value,
            "reconciliation_gap": reconciliation_gap,
            "traceability_status": traceability_status,
        })

    production_count = batches.count()
    portions_produced = batches.aggregate(total=Sum("quantity_produced"))["total"] or 0
    production_cost = sum((batch.total_cost() for batch in batches), Decimal("0.00"))
    for batch in batches:
        day = batch.created_at.date()
        if day in daily:
            daily[day]["production"] += batch.quantity_produced

    spoilage_records = KitchenSpoilageRecord.objects.filter(
        department__hotel__in=scoped_hotels,
        recorded_at__date__gte=date_from,
        recorded_at__date__lte=date_to,
    )
    spoilage_cost = spoilage_records.aggregate(total=Sum("cost_amount"))["total"] or Decimal("0.00")
    spoilage_quantity = spoilage_records.aggregate(total=Sum("quantity"))["total"] or Decimal("0.000")
    pending_spoilage = spoilage_records.filter(review_status="PENDING").count()
    for record in spoilage_records:
        day = record.recorded_at.date()
        if day in daily:
            daily[day]["spoilage"] += record.cost_amount

    trend_rows = []
    max_sales = max((row["sales"] for row in daily.values()), default=Decimal("0.00"))
    max_production = max((row["production"] for row in daily.values()), default=0)
    max_spoilage = max((row["spoilage"] for row in daily.values()), default=Decimal("0.00"))
    for day, values in daily.items():
        trend_rows.append({
            "date": day,
            "label": day.strftime("%b %d"),
            "sales": values["sales"],
            "production": values["production"],
            "spoilage": values["spoilage"],
            "sales_width": int(values["sales"] / max_sales * 100) if max_sales else 0,
            "production_width": int(values["production"] / max_production * 100) if max_production else 0,
            "spoilage_width": int(values["spoilage"] / max_spoilage * 100) if max_spoilage else 0,
        })

    requests = IngredientRestockRequest.objects.filter(
        requested_by__department__hotel__in=scoped_hotels,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    pending_requests = requests.filter(status__in=("PENDING", "APPROVED", "RECONCILIATION_PENDING", "PARTIALLY_ISSUED")).count()

    direct_purchases = DirectPurchase.objects.filter(
        supplier__hotel__in=scoped_hotels,
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )
    pending_purchases = direct_purchases.filter(
        status__in=("RECONCILIATION_PENDING", "APPROVED", "PAID", "PARTIALLY_RECEIVED")
    ).count()
    outstanding_delivery_quantity = sum(
        (item.outstanding_quantity for item in DirectPurchaseItem.objects.filter(
            purchase__in=direct_purchases,
            purchase__status__in=("APPROVED", "PAID", "PARTIALLY_RECEIVED"),
        )),
        Decimal("0.000"),
    )

    return render(request, "kitchen/management_report.html", {
        "hotels": hotels.order_by("name"),
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_sales": total_sales,
        "sales_cogs": sales_cogs,
        "estimated_gross_profit": total_sales - sales_cogs,
        "total_sold": total_sold,
        "production_count": production_count,
        "portions_produced": portions_produced,
        "production_cost": production_cost,
        "spoilage_cost": spoilage_cost,
        "spoilage_quantity": spoilage_quantity,
        "pending_spoilage": pending_spoilage,
        "pending_requests": pending_requests,
        "pending_purchases": pending_purchases,
        "outstanding_delivery_quantity": outstanding_delivery_quantity,
        "uncosted_sales_items": sales_items.filter(cost_at_sale__lte=0).count(),
        "trend_rows": trend_rows,
        "batch_profitability_rows": batch_profitability_rows,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def kitchen_spoilage_report(request):
    """Management audit register for recorded Kitchen spoilage within authorized hotel scope."""
    from django.db.models import Sum

    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied("You do not have access to Kitchen reports.")

    today = timezone.localdate()
    try:
        date_from = date.fromisoformat(request.GET.get("from", "").strip()) if request.GET.get("from") else today.replace(day=1)
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = date.fromisoformat(request.GET.get("to", "").strip()) if request.GET.get("to") else today
    except ValueError:
        date_to = today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel.isdigit():
        scoped_hotels = hotels.filter(pk=int(selected_hotel))
        if not scoped_hotels.exists():
            raise PermissionDenied("You do not have access to that hotel.")
    else:
        scoped_hotels, selected_hotel = hotels, ""

    records = KitchenSpoilageRecord.objects.filter(
        department__hotel__in=scoped_hotels,
        recorded_at__date__gte=date_from,
        recorded_at__date__lte=date_to,
    ).select_related(
        "product", "department", "department__hotel",
        "recorded_by", "reviewed_by", "stock_movement",
    )

    status_filter = request.GET.get("status", "").strip()
    valid_statuses = {value for value, _ in KitchenSpoilageRecord.REVIEW_STATUS_CHOICES}
    if status_filter in valid_statuses:
        records = records.filter(review_status=status_filter)
    else:
        status_filter = ""

    reason_filter = request.GET.get("reason", "").strip()
    valid_reasons = {value for value, _ in KitchenSpoilageRecord.REASON_CHOICES}
    if reason_filter in valid_reasons:
        records = records.filter(reason_code=reason_filter)
    else:
        reason_filter = ""

    product_filter = request.GET.get("product", "").strip()
    if product_filter.isdigit():
        records = records.filter(product_id=int(product_filter))
    else:
        product_filter = ""

    summary = records.aggregate(total_cost=Sum("cost_amount"), total_quantity=Sum("quantity"))
    return render(request, "kitchen/reports/spoilage_report.html", {
        "hotels": hotels.order_by("name"),
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "records": records.order_by("-recorded_at", "-id"),
        "total_records": records.count(),
        "total_cost": summary["total_cost"] or Decimal("0.00"),
        "total_quantity": summary["total_quantity"] or Decimal("0.000"),
        "pending_count": records.filter(review_status="PENDING").count(),
        "status_filter": status_filter,
        "reason_filter": reason_filter,
        "product_filter": product_filter,
        "status_choices": KitchenSpoilageRecord.REVIEW_STATUS_CHOICES,
        "reason_choices": KitchenSpoilageRecord.REASON_CHOICES,
        "products": Product.objects.filter(hotel__in=scoped_hotels, product_type__in=("RAW", "FOOD")).order_by("name").distinct(),
    })


def kitchen_food_sales_report(request):
    """Head-chef-only food sales audit for this Kitchen's own hotel."""
    import csv
    from django.http import HttpResponse
    from django.contrib.auth import get_user_model
    from restaurant.models import POSOrderItem, MenuItem

    user = request.user
    kitchen = user.department
    if not user.is_department_head or not kitchen or kitchen.department_type != "KITCHEN":
        raise PermissionDenied("Only the Head Chef can access this report.")

    UserModel = get_user_model()
    hotel_id = kitchen.hotel_id

    rows = POSOrderItem.objects.filter(
        order__department__hotel_id=hotel_id,
        menu_item__product__product_type="FOOD",
        order__status__in=["CHARGED", "PAID"],
        order__is_refunded=False,
    ).select_related(
        "order", "order__created_by", "order__shift",
        "menu_item", "menu_item__product",
        "order__kitchen_ticket", "order__kitchen_ticket__accepted_by",
    ).order_by("-order__created_at", "-order_id", "menu_item__name")

    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    restaurant_staff_id = request.GET.get("restaurant_staff", "").strip()
    kitchen_staff_id = request.GET.get("kitchen_staff", "").strip()
    food_item_id = request.GET.get("food_item", "").strip()
    order_number = request.GET.get("order_number", "").strip()

    if date_from:
        try:
            rows = rows.filter(order__created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            rows = rows.filter(order__created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""

    restaurant_staff = UserModel.objects.filter(
        role="RESTAURANT", department__hotel_id=hotel_id
    ).order_by("first_name", "last_name", "username")
    kitchen_staff = UserModel.objects.filter(
        role="KITCHEN", department__hotel_id=hotel_id
    ).order_by("first_name", "last_name", "username")
    food_items = MenuItem.objects.filter(
        product__hotel_id=hotel_id,
        product__product_type="FOOD",
    ).order_by("name")

    if restaurant_staff_id.isdigit():
        rows = rows.filter(order__created_by_id=int(restaurant_staff_id))
    else:
        restaurant_staff_id = ""

    if kitchen_staff_id == "unrecorded":
        rows = rows.filter(order__kitchen_ticket__accepted_by__isnull=True)
    elif kitchen_staff_id.isdigit():
        rows = rows.filter(order__kitchen_ticket__accepted_by_id=int(kitchen_staff_id))
    else:
        kitchen_staff_id = ""

    if food_item_id.isdigit():
        rows = rows.filter(menu_item_id=int(food_item_id))
    else:
        food_item_id = ""

    if order_number:
        if order_number.isdigit():
            rows = rows.filter(order_id=int(order_number))
        else:
            rows = rows.none()

    sales_rows = []
    total_quantity = 0
    total_revenue = Decimal("0.00")
    for row in rows:
        ticket = getattr(row.order, "kitchen_ticket", None)
        kitchen_person = ticket.accepted_by if ticket and ticket.accepted_by_id else None
        revenue = row.quantity * row.price
        total_quantity += row.quantity
        total_revenue += revenue
        sales_rows.append({
            "item": row.menu_item.name,
            "quantity": row.quantity,
            "unit_price": row.price,
            "revenue": revenue,
            "order": row.order,
            "restaurant_staff": row.order.created_by,
            "kitchen_staff": kitchen_person,
        })

    if request.GET.get("export") == "pdf":
        from io import BytesIO
        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="restaurant-food-sales-audit.pdf"'
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            rightMargin=12 * mm, leftMargin=12 * mm,
            topMargin=10 * mm, bottomMargin=12 * mm,
            title="Restaurant Food Sales Audit",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="AuditHotel", parent=styles["Title"], fontName="Helvetica-Bold",
            fontSize=17, leading=20, alignment=TA_LEFT, textColor=colors.HexColor("#172033"),
            spaceAfter=3,
        ))
        styles.add(ParagraphStyle(
            name="AuditSmall", parent=styles["Normal"], fontSize=8, leading=10,
            textColor=colors.HexColor("#475569"),
        ))
        styles.add(ParagraphStyle(
            name="AuditCell", parent=styles["Normal"], fontSize=7, leading=8,
        ))
        styles.add(ParagraphStyle(
            name="AuditCellBold", parent=styles["Normal"], fontName="Helvetica-Bold",
            fontSize=7, leading=8,
        ))
        story = []
        hotel = kitchen.hotel
        organization = hotel.organization
        header_left = []
        if organization and organization.logo:
            try:
                organization.logo.open("rb")
                logo_bytes = organization.logo.read()
                organization.logo.close()
                logo = Image(BytesIO(logo_bytes), width=24 * mm, height=18 * mm, kind="proportional")
                header_left.append(logo)
            except Exception:
                pass
        header_left.extend([
            Paragraph(hotel.name, styles["AuditHotel"]),
            Paragraph(hotel.location or "Hotel address/location not configured", styles["AuditSmall"]),
            Paragraph(organization.name if organization else "", styles["AuditSmall"]),
        ])
        header = Table([[header_left]], colWidths=[doc.width])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
        ]))
        story.append(header)
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("RESTAURANT FOOD SALES AUDIT", styles["Heading1"]))
        story.append(Paragraph(
            f"Generated {timezone.localtime().strftime('%d %b %Y, %H:%M')} · "
            f"Reporting period: {date_from or 'Beginning'} to {date_to or 'Today'}",
            styles["AuditSmall"],
        ))
        filter_parts = []
        if restaurant_staff_id:
            selected = restaurant_staff.filter(pk=restaurant_staff_id).first()
            filter_parts.append("Restaurant staff: " + (selected.get_full_name() or selected.username if selected else restaurant_staff_id))
        if kitchen_staff_id:
            if kitchen_staff_id == "unrecorded":
                filter_parts.append("Kitchen staff: Not recorded")
            else:
                selected = kitchen_staff.filter(pk=kitchen_staff_id).first()
                filter_parts.append("Kitchen staff: " + (selected.get_full_name() or selected.username if selected else kitchen_staff_id))
        if food_item_id:
            selected = food_items.filter(pk=food_item_id).first()
            filter_parts.append("Food item: " + (selected.name if selected else food_item_id))
        if order_number:
            filter_parts.append("Order: #" + order_number)
        story.append(Paragraph(
            "Applied filters: " + (" · ".join(filter_parts) if filter_parts else "None"),
            styles["AuditSmall"],
        ))
        story.append(Spacer(1, 4 * mm))
        summary = Table(
            [[
                Paragraph(f"<b>Food portions sold</b><br/>{total_quantity}", styles["Normal"]),
                Paragraph(f"<b>Food sales revenue</b><br/>NGN {total_revenue:,.2f}", styles["Normal"]),
                Paragraph(f"<b>Sale lines</b><br/>{len(sales_rows)}", styles["Normal"]),
            ]],
            colWidths=[doc.width / 3] * 3,
        )
        summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(summary)
        story.append(Spacer(1, 5 * mm))
        headers = ["Date / time", "Order", "Shift", "Food item", "Qty", "Unit price", "Revenue", "Restaurant staff", "Kitchen staff recorded"]
        data = [[Paragraph(f"<b>{h}</b>", styles["AuditCell"]) for h in headers]]
        for row in sales_rows:
            restaurant_person = row["restaurant_staff"]
            kitchen_person = row["kitchen_staff"]
            restaurant_name = (restaurant_person.get_full_name() or restaurant_person.username) if restaurant_person else "Account unavailable"
            kitchen_name = (kitchen_person.get_full_name() or kitchen_person.username) if kitchen_person else "Not recorded"
            values = [
                row["order"].created_at.strftime("%d %b %Y %H:%M"),
                f"#{row['order'].id} ({row['order'].get_status_display()})",
                f"#{row['order'].shift_id}" if row["order"].shift_id else "—",
                row["item"], str(row["quantity"]),
                f"NGN {row['unit_price']:,.2f}", f"NGN {row['revenue']:,.2f}",
                restaurant_name, kitchen_name,
            ]
            data.append([Paragraph(str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles["AuditCell"]) for value in values])
        widths = [25*mm, 22*mm, 13*mm, 27*mm, 10*mm, 22*mm, 23*mm, 35*mm, 35*mm]
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#334155")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.append(table)
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "Audit note: Kitchen staff identifies the person recorded as accepting/starting the ticket. "
            "It is not proof that this person alone cooked every item. Historical records may not contain attribution.",
            styles["AuditSmall"],
        ))
        doc.build(story)
        response.write(buffer.getvalue())
        return response

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="restaurant-food-sales-audit.csv"'
        response.write("\\ufeff")
        writer = csv.writer(response)
        writer.writerow([
            "Date/time", "Order number", "Order status", "Shift ID",
            "Food item", "Quantity", "Unit price", "Revenue",
            "Restaurant staff", "Kitchen staff recorded",
        ])
        for row in sales_rows:
            restaurant_person = row["restaurant_staff"]
            kitchen_person = row["kitchen_staff"]
            writer.writerow([
                row["order"].created_at.strftime("%Y-%m-%d %H:%M"),
                row["order"].id,
                row["order"].get_status_display(),
                row["order"].shift_id or "",
                row["item"],
                row["quantity"],
                row["unit_price"],
                row["revenue"],
                (restaurant_person.get_full_name() or restaurant_person.username)
                if restaurant_person else "User account unavailable",
                (kitchen_person.get_full_name() or kitchen_person.username)
                if kitchen_person else "Not recorded",
            ])
        return response

    return render(request, "kitchen/food_sales_report.html", {
        "sales_rows": sales_rows,
        "date_from": date_from,
        "date_to": date_to,
        "restaurant_staff_options": restaurant_staff,
        "kitchen_staff_options": kitchen_staff,
        "food_item_options": food_items,
        "selected_restaurant_staff": restaurant_staff_id,
        "selected_kitchen_staff": kitchen_staff_id,
        "selected_food_item": food_item_id,
        "order_number": order_number,
        "total_quantity": total_quantity,
        "total_revenue": total_revenue,
    })


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

    kitchen = request.user.department
    if (
        not kitchen
        or kitchen.department_type != "KITCHEN"
        or kitchen.hotel_id != dp.supplier.hotel_id
        or not dp.requested_by
        or dp.requested_by.department_id != kitchen.id
    ):
        raise PermissionDenied

    if dp.requested_by_id != request.user.id and not request.user.is_department_head:
        raise PermissionDenied

    items = list(dp.items.select_for_update().select_related("product"))
    if not items:
        messages.error(request, "This direct purchase has no items to receive.")
        return redirect("kitchen_incoming_deliveries")

    from accounting.services.direct_purchase_accounting import (
        direct_purchase_paid_value,
        direct_purchase_received_value,
        post_direct_purchase_receipt,
    )
    prior_received_value = direct_purchase_received_value(dp)
    paid_to_date = direct_purchase_paid_value(dp.supplier.hotel, dp)
    advance_remaining = max(
        paid_to_date - prior_received_value,
        Decimal("0.00"),
    )

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

        # Recognize only goods physically received. If the supplier was paid
        # in advance, apply that asset first; any remaining value becomes AP.
        advance_applied = post_direct_purchase_receipt(
            purchase=dp,
            item=item,
            received_now=received_now,
            advance_available=advance_remaining,
            created_by=request.user,
        )
        advance_remaining = max(
            advance_remaining - advance_applied,
            Decimal("0.00"),
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

@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
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

    pending_overrides = ProductionBatch.objects.none()
    if request.user.role in ("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN"):
        pending_overrides = (
            ProductionBatch.objects
            .filter(
                override_status="PENDING",
                is_executed=False,
                recipe__product__hotel__in=hotels,
            )
            .select_related("recipe", "produced_by", "override_requested_by")
            .order_by("created_at")
        )

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
            "pending_overrides": pending_overrides,
        }
    )

@role_required("KITCHEN")
@require_POST
@transaction.atomic
def recipe_version_draft_create(request, recipe_id):
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can create recipe versions.")

    hotels = get_accessible_hotels(request.user)
    recipe = get_object_or_404(
        Recipe.objects.select_for_update().select_related("product"),
        pk=recipe_id,
        product__hotel__in=hotels,
    )
    if not request.user.department or request.user.department.department_type != "KITCHEN":
        raise PermissionDenied("A Kitchen Department Head is required.")

    latest_number = recipe.versions.order_by("-version_number").values_list(
        "version_number", flat=True
    ).first() or 0
    version = RecipeVersion.objects.create(
        recipe=recipe,
        version_number=latest_number + 1,
        status="DRAFT",
        created_by=request.user,
        change_note=request.POST.get("change_note", "").strip(),
    )

    # Once a recipe has an approved snapshot, that snapshot is the source
    # of truth for the next draft. Legacy RecipeItem rows are only the
    # bootstrap source for recipes that have never been versioned.
    if recipe.active_version_id:
        source_items = recipe.active_version.items.select_related("ingredient").all()
    else:
        source_items = recipe.items.select_related("ingredient").all()

    for item in source_items:
        RecipeVersionItem.objects.create(
            recipe_version=version,
            ingredient=item.ingredient,
            quantity=item.quantity,
            unit=(getattr(item, "unit", "") or getattr(item.ingredient, "base_unit", "") or ""),
            control_mode=item.control_mode,
            tolerance_percent=item.tolerance_percent,
            tolerance_quantity=item.tolerance_quantity,
        )

    messages.success(
        request,
        f"Draft version {version.version_number} created. Review it before approval."
    )
    return redirect("kitchen_recipe_version_history", recipe_id=recipe.pk)


@role_required("KITCHEN")
@require_POST
@transaction.atomic
def recipe_version_item_update(request, version_id, item_id):
    """Edit one ingredient on a draft only; persist converted base-unit quantity."""
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can edit recipe drafts.")

    hotels = get_accessible_hotels(request.user)
    version = get_object_or_404(
        RecipeVersion.objects.select_for_update().select_related("recipe__product"),
        pk=version_id,
        recipe__product__hotel__in=hotels,
    )
    if (
        version.status != "DRAFT"
        or not request.user.department
        or request.user.department.department_type != "KITCHEN"
        or request.user.department.hotel_id != version.recipe.product.hotel_id
    ):
        raise PermissionDenied("Only your hotel's Kitchen Head can edit draft recipe versions.")

    item = get_object_or_404(
        RecipeVersionItem.objects.select_for_update().select_related("ingredient"),
        pk=item_id,
        recipe_version=version,
    )
    try:
        quantity = Decimal(request.POST.get("quantity", ""))
        if not quantity.is_finite() or quantity <= 0:
            raise ValidationError("Quantity must be a finite number greater than zero.")
        entered_unit = request.POST.get("unit", "").strip()
        base_unit = item.ingredient.base_unit.strip()
        from .unit_conversion import convert_quantity
        base_quantity = convert_quantity(quantity, entered_unit, base_unit)
        if base_quantity <= 0:
            raise ValidationError("Converted quantity must be greater than zero.")
    except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc) or "Enter a valid quantity and unit.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    update_fields = ["quantity", "unit"]
    item.quantity = base_quantity
    item.unit = base_unit

    # Keep compatibility with existing quantity-only forms, while allowing
    # the recipe editor to update variance controls when those fields are posted.
    if "control_mode" in request.POST:
        control_mode = request.POST.get("control_mode", "").strip()
        if control_mode not in {"STRICT", "TOLERANCE", "FLEXIBLE"}:
            messages.error(request, "Choose a valid recipe control mode.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        item.control_mode = control_mode
        update_fields.append("control_mode")
    if "tolerance_percent" in request.POST:
        try:
            tolerance = Decimal(request.POST.get("tolerance_percent", "0"))
            if not tolerance.is_finite() or tolerance < 0 or tolerance > 100:
                raise ValidationError("Percentage tolerance must be between 0 and 100.")
        except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
            messages.error(request, str(exc) or "Enter a valid percentage tolerance.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        item.tolerance_percent = tolerance
        update_fields.append("tolerance_percent")
    if "tolerance_quantity" in request.POST:
        try:
            absolute_raw = request.POST.get("tolerance_quantity", "").strip()
            absolute_tolerance = Decimal(absolute_raw) if absolute_raw else None
            if absolute_tolerance is not None and (
                not absolute_tolerance.is_finite() or absolute_tolerance < 0
            ):
                raise ValidationError("Absolute quantity tolerance must be finite and non-negative.")
        except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
            messages.error(request, str(exc) or "Enter a valid absolute tolerance.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        item.tolerance_quantity = absolute_tolerance
        update_fields.append("tolerance_quantity")

    item.save(update_fields=update_fields)
    messages.success(
        request,
        f"Updated {item.ingredient.name}: {base_quantity} {base_unit} per portion."
    )
    return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)


@role_required("KITCHEN")
@require_POST
@transaction.atomic
def recipe_version_item_add(request, version_id):
    """Add an ingredient to a draft snapshot without modifying the live recipe."""
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can edit recipe drafts.")

    hotels = get_accessible_hotels(request.user)
    version = get_object_or_404(
        RecipeVersion.objects.select_for_update().select_related("recipe__product"),
        pk=version_id,
        recipe__product__hotel__in=hotels,
    )
    if (
        version.status != "DRAFT"
        or not request.user.department
        or request.user.department.department_type != "KITCHEN"
        or request.user.department.hotel_id != version.recipe.product.hotel_id
    ):
        raise PermissionDenied("Only your hotel's Kitchen Head can edit draft recipe versions.")

    try:
        ingredient = Product.objects.get(
            pk=request.POST.get("ingredient_id", ""),
            hotel_id=version.recipe.product.hotel_id,
            product_type="RAW",
        )
    except (Product.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Choose a valid raw ingredient from this hotel.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    if version.items.filter(ingredient=ingredient).exists():
        messages.error(request, f"{ingredient.name} is already in this draft version.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    try:
        quantity = Decimal(request.POST.get("quantity", ""))
        entered_unit = request.POST.get("unit", "").strip()
        base_unit = (ingredient.base_unit or "").strip()
        from .unit_conversion import convert_quantity
        if not quantity.is_finite() or quantity <= 0:
            raise ValidationError("Quantity must be a finite number greater than zero.")
        base_quantity = convert_quantity(quantity, entered_unit, base_unit)
        if base_quantity <= 0:
            raise ValidationError("Converted quantity must be greater than zero.")

        control_mode = request.POST.get("control_mode", "STRICT").strip()
        if control_mode not in {"STRICT", "TOLERANCE", "FLEXIBLE"}:
            raise ValidationError("Choose a valid recipe control mode.")
        tolerance = Decimal(request.POST.get("tolerance_percent", "0"))
        if not tolerance.is_finite() or tolerance < 0 or tolerance > 100:
            raise ValidationError("Percentage tolerance must be between 0 and 100.")
        absolute_raw = request.POST.get("tolerance_quantity", "").strip()
        absolute_tolerance = Decimal(absolute_raw) if absolute_raw else None
        if absolute_tolerance is not None and (
            not absolute_tolerance.is_finite() or absolute_tolerance < 0
        ):
            raise ValidationError("Absolute quantity tolerance must be finite and non-negative.")
    except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc) or "Check the ingredient quantity, unit, and control settings.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    RecipeVersionItem.objects.create(
        recipe_version=version,
        ingredient=ingredient,
        quantity=base_quantity,
        unit=base_unit,
        control_mode=control_mode,
        tolerance_percent=tolerance,
        tolerance_quantity=absolute_tolerance,
    )
    messages.success(request, f"Added {ingredient.name} to draft version {version.version_number}.")
    return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)


@role_required("KITCHEN")
@require_POST
@transaction.atomic
def recipe_version_item_remove(request, version_id, item_id):
    """Remove an ingredient from a draft snapshot only."""
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can edit recipe drafts.")

    hotels = get_accessible_hotels(request.user)
    version = get_object_or_404(
        RecipeVersion.objects.select_for_update().select_related("recipe__product"),
        pk=version_id,
        recipe__product__hotel__in=hotels,
    )
    if (
        version.status != "DRAFT"
        or not request.user.department
        or request.user.department.department_type != "KITCHEN"
        or request.user.department.hotel_id != version.recipe.product.hotel_id
    ):
        raise PermissionDenied("Only your hotel's Kitchen Head can edit draft recipe versions.")

    item = get_object_or_404(
        RecipeVersionItem.objects.select_for_update(),
        pk=item_id,
        recipe_version=version,
    )
    ingredient_name = item.ingredient.name
    item.delete()
    messages.success(request, f"Removed {ingredient_name} from draft version {version.version_number}.")
    return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)


@role_required("KITCHEN")
@require_POST
@transaction.atomic
def recipe_version_approve(request, version_id):
    if not request.user.is_department_head:
        raise PermissionDenied("Only the Kitchen Department Head can approve recipe versions.")

    hotels = get_accessible_hotels(request.user)
    version = get_object_or_404(
        RecipeVersion.objects.select_for_update().select_related("recipe__product"),
        pk=version_id,
        recipe__product__hotel__in=hotels,
    )
    if (
        not request.user.department
        or request.user.department.department_type != "KITCHEN"
        or request.user.department.hotel_id != version.recipe.product.hotel_id
    ):
        raise PermissionDenied("You can only approve recipes for your own hotel's kitchen.")

    if version.status != "DRAFT":
        messages.error(request, "Only draft recipe versions can be approved.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    items = list(version.items.select_related("ingredient"))
    if not items:
        messages.error(request, "A recipe version must contain at least one ingredient.")
        return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    for item in items:
        if item.quantity <= 0:
            messages.error(request, f"Quantity for {item.ingredient.name} must be greater than zero.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        if item.ingredient.product_type != "RAW":
            messages.error(request, f"{item.ingredient.name} is not a RAW ingredient.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        if item.ingredient.hotel_id != version.recipe.product.hotel_id:
            messages.error(request, "Recipe ingredients and finished food must belong to the same hotel.")
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)
        if not item.ingredient.departments.filter(pk=request.user.department_id).exists():
            messages.error(
                request,
                f"{item.ingredient.name} is not assigned to this kitchen.",
            )
            return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)

    now = timezone.now()
    RecipeVersion.objects.filter(
        recipe=version.recipe,
        status="APPROVED",
    ).exclude(pk=version.pk).update(status="SUPERSEDED")

    version.status = "APPROVED"
    version.approved_by = request.user
    version.approved_at = now
    version.save(update_fields=["status", "approved_by", "approved_at"])

    version.recipe.active_version = version
    version.recipe.version = version.version_number
    version.recipe.save(update_fields=["active_version", "version"])

    messages.success(request, f"Recipe version {version.version_number} approved and activated.")
    return redirect("kitchen_recipe_version_history", recipe_id=version.recipe_id)


@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def recipe_version_history(request, recipe_id):
    hotels = get_accessible_hotels(request.user)
    recipe = get_object_or_404(
        Recipe.objects.select_related("product", "active_version"),
        pk=recipe_id,
        product__hotel__in=hotels,
    )

    # Kitchen users can only view recipes belonging to their own hotel.
    if request.user.role == "KITCHEN":
        department = request.user.department
        if not department or department.hotel_id != recipe.product.hotel_id:
            raise PermissionDenied

    versions = (
        recipe.versions
        .select_related("created_by", "approved_by")
        .prefetch_related("items__ingredient")
        .order_by("-version_number")
    )
    available_ingredients = Product.objects.filter(
        hotel_id=recipe.product.hotel_id,
        product_type="RAW",
    ).order_by("name")
    if hasattr(Product, "is_active"):
        available_ingredients = available_ingredients.filter(is_active=True)

    return render(
        request,
        "kitchen/recipe_version_history.html",
        {
            "recipe": recipe,
            "versions": versions,
            "available_ingredients": available_ingredients,
        },
    )


@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
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

    from django.db.models import Sum

    production_cost = batch.total_cost()
    variance_cost_estimate = batch.calculate_variance_cost()
    allocations = (
        StockMovementCostAllocation.objects
        .filter(
            movement__reference=f"PROD-{batch.pk}",
            movement__movement_type="OUT",
        )
        .values("cost_layer__product_id")
        .annotate(total_cost=Sum("total_cost"), total_quantity=Sum("quantity"))
    )
    allocations_by_ingredient = {
        row["cost_layer__product_id"]: row
        for row in allocations
    }

    ingredient_cost_rows = []
    for usage in batch.ingredient_usages.all():
        allocation = allocations_by_ingredient.get(usage.ingredient_id, {})
        ingredient_cost_rows.append({
            "usage": usage,
            "fifo_cost": allocation.get("total_cost") or Decimal("0.00"),
            "costed_quantity": allocation.get("total_quantity") or Decimal("0.000"),
            "estimated_variance_cost": (
                max(usage.variance, Decimal("0.000")) * usage.ingredient.cost_price
            ),
        })

    return render(
        request,
        "kitchen/production/detail.html",
        {
            "batch": batch,
            "production_cost": production_cost,
            "cost_per_portion": (
                production_cost / Decimal(batch.quantity_produced)
                if batch.quantity_produced else Decimal("0.00")
            ),
            "variance_cost_estimate": variance_cost_estimate,
            "ingredient_cost_rows": ingredient_cost_rows,
            "has_uncosted_usage": batch.ingredient_usages.filter(uncosted_quantity__gt=0).exists(),
        }
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
    ticket.accepted_by = request.user

    ticket.save(update_fields=["status", "eta_minutes", "accepted_by"])

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