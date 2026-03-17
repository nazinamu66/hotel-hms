from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied
from core.utils import get_user_hotels
from accounts.decorators import role_required
from .models import Supplier, PurchaseOrder, PurchaseItem, Department, LowStockRequest,StockMovement,Stock,Product,HotelFeature
from .forms import SupplierForm, PurchaseOrderForm, PurchaseItemForm,ProductForm
from .permissions import is_admin, is_manager, is_store
from inventory.models import transfer_stock
from kitchen.forms import (
    PreparedFoodForm,
    RecipeItemForm,

)

from kitchen.models import (
    Recipe,
    RecipeItem,
)
# =========================
# SUPPLIERS
# =========================

@role_required("ADMIN", "DIRECTOR")
def supplier_list(request):

    suppliers = Supplier.objects.all()

    return render(
        request,
        "inventory/supplier_list.html",
        {"suppliers": suppliers}
    )


@role_required("ADMIN", "DIRECTOR")
def supplier_create(request):

    form = SupplierForm(request.POST or None)

    if form.is_valid():
        form.save()
        messages.success(request, "Supplier created successfully.")
        return redirect("inventory:supplier_list")

    return render(
        request,
        "inventory/supplier_form.html",
        {"form": form}
    )

@role_required("DIRECTOR")
def product_list(request):

    product_type = request.GET.get("type")

    products = Product.objects.filter(is_active=True)

    if product_type:
        products = products.filter(product_type=product_type)

    # products = products.order_by("name")
    products = products.order_by("product_type", "name")
    context = {
        "products": products,
        "product_types": Product.PRODUCT_TYPE,
        "selected_type": product_type
    }

    return render(
        request,
        "inventory/product_list.html",
        context
    )

from django.db import IntegrityError


@role_required("DIRECTOR")
def product_create(request):

    form = ProductForm(request.POST or None)

    if form.is_valid():

        form.save()

        messages.success(request, "Product created successfully.")

        return redirect("inventory:product_list")

    return render(
        request,
        "inventory/product_form.html",
        {"form": form}
    )

@role_required("DIRECTOR")
def hotel_feature_setup(request):

    hotel = request.user.hotel

    features = dict(HotelFeature.FEATURE_CHOICES)

    if request.method == "POST":

        selected = request.POST.getlist("features")

        HotelFeature.objects.filter(hotel=hotel).delete()

        for f in selected:
            HotelFeature.objects.create(
                hotel=hotel,
                feature=f
            )

        messages.success(request, "Hotel features updated.")

        return redirect("owner_dashboard")

    active_features = HotelFeature.objects.filter(
        hotel=hotel
    ).values_list("feature", flat=True)

    return render(
        request,
        "inventory/hotel_features.html",
        {
            "features": features,
            "active_features": active_features
        }
    )

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def prepared_food_create(request):

    form = PreparedFoodForm(request.POST or None)

    if form.is_valid():
        food = form.save(commit=False)

        # enforce system rules
        food.product_type = "FOOD"
        food.base_unit = "portion"
        food.purchase_unit = "portion"
        food.unit_multiplier = 1

        food.save()

        # auto create menu item
        from restaurant.models import MenuItem

        MenuItem.objects.get_or_create(
            product=food,
            defaults={
                "name": food.name,
                "price": 0,
                "category": "FOOD",
                "is_active": True
            }
        )

        messages.success(
            request,
            f"{food.name} created. Now add its recipe."
        )

        return redirect("kitchen_food_list")

    return render(
        request,
        "inventory/setup/foods/create.html",
        {"form": form}
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def recipe_edit(request, food_id):

    food = get_object_or_404(Product, id=food_id)

    recipe, _ = Recipe.objects.get_or_create(
        product=food,
        defaults={"name": food.name}
    )

    items = recipe.items.select_related(
        "ingredient"
    ).order_by("ingredient__name")

    return render(
        request,
        "inventory/setup/recipes/edit.html",
        {
            "food": food,
            "recipe": recipe,
            "items": items,
        }
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def recipe_item_delete(request, item_id):

    item = get_object_or_404(RecipeItem, id=item_id)
    recipe = item.recipe

    # 🔒 Soft Lock
    if recipe.productionbatch_set.exists():
        messages.error(
            request,
            "This recipe has production history. "
            "Create a new version instead of modifying it."
        )
        return redirect("inventory:kitchen_recipe_edit", food_id=recipe.product.id)

    item.delete()

    messages.success(
        request,
        f"{item.ingredient.name} removed from recipe."
    )

    return redirect("inventory:kitchen_recipe_edit", food_id=recipe.product.id)


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def recipe_item_add(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)

    if recipe.productionbatch_set.exists():
        messages.error(
            request,
            "This recipe has production history. "
            "Create a new version instead of modifying it."
        )
        return redirect("inventory:kitchen_recipe_edit", food_id=recipe.product.id)
    if request.method == "POST":
        form = RecipeItemForm(request.POST)

        if form.is_valid():
            item = form.save(commit=False)
            item.recipe = recipe
            item.created_by = request.user
            item.save()

            messages.success(request, "Ingredient added.")
            return redirect("inventory:kitchen_recipe_edit", food_id=recipe.product.id)

    else:
        form = RecipeItemForm()

    return render(
        request,
        "inventory/setup/recipes/add_item.html",
        {
            "recipe": recipe,
            "form": form,
        }
    )
# =========================
# PURCHASE ORDERS
# =========================

@role_required("STORE", "MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT")
def po_list(request):

    hotel = get_user_hotels(request.user)

    qs = PurchaseOrder.objects.select_related("supplier", "department")

    if request.user.role == "STORE":
        qs = qs.filter(
            department=request.user.department,
            status__in=["PAID", "RECEIVED"]
        )

    elif hotel:
        qs = qs.filter(department__hotel=hotel)

    return render(request, "inventory/po_list.html", {"pos": qs})


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def po_create(request):
    form = PurchaseOrderForm(request.POST or None)

    if form.is_valid():
        po = form.save(commit=False)
        po.created_by = request.user
        po.status = "DRAFT"

        # 🔒 ENFORCE STORE AS RECEIVING DEPARTMENT
        hotel = get_user_hotels(request.user)

        po.department = Department.objects.get(
            hotel=hotel,
            department_type="STORE"
        )

        po.save()
        messages.success(request, "Draft Purchase Order created.")
        return redirect("inventory:po_detail", pk=po.pk)

    return render(request, "inventory/po_form.html", {"form": form})


@role_required("STORE", "MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT")
def po_detail(request, pk):
    hotel = get_user_hotels(request.user)

    po = get_object_or_404(
        PurchaseOrder,
        pk=pk,
    )

    if hotel and po.department.hotel != hotel:
        raise PermissionDenied

    if request.user.role == "STORE" and po.status not in ["PAID", "RECEIVED"]:
        raise PermissionDenied

    item_form = PurchaseItemForm(request.POST or None)

    if request.method == "POST" and po.status == "DRAFT":
        if not (is_admin(request.user) or is_manager(request.user)):
            raise PermissionDenied

        if item_form.is_valid():
            item = item_form.save(commit=False)
            item.purchase_order = po
            item.save()
            messages.success(request, "Item added.")
            return redirect("inventory:po_detail", pk=pk)

    return render(
        request,
        "inventory/po_detail.html",
        {"po": po, "item_form": item_form}
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def po_submit(request, pk):
    hotel = get_user_hotels(request.user)

    po = get_object_or_404(PurchaseOrder, pk=pk, status="DRAFT")

    if hotel and po.department.hotel != hotel:
        raise PermissionDenied

    if not po.items.exists():
        messages.error(request, "Add at least one item.")
        return redirect("inventory:po_detail", pk=pk)

    po.status = "SUBMITTED"
    po.save(update_fields=["status"])

    messages.success(request, "Purchase Order submitted for payment.")
    return redirect("inventory:po_detail", pk=pk)


@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def po_finalize(request, pk):
    hotel = get_user_hotels(request.user)

    po = get_object_or_404(PurchaseOrder, pk=pk, status="DRAFT")
    if hotel and po.department.hotel != hotel:
        raise PermissionDenied
    
    items = po.items.select_related("product")

    if request.method == "POST":
        supplier_id = request.POST.get("supplier")

        if not supplier_id:
            messages.error(request, "Supplier is required.")
            return redirect(request.path)

        po.supplier = get_object_or_404(Supplier, id=supplier_id)

        for item in items:
            qty = int(request.POST.get(f"qty_{item.id}", 0))
            cost = request.POST.get(f"cost_{item.id}", 0)

            if qty <= 0:
                item.delete()
            else:
                item.purchase_quantity = qty
                item.unit_cost = cost
                item.save(update_fields=["purchase_quantity", "unit_cost"])

        if not po.items.exists():
            messages.error(request, "PO must contain at least one item.")
            return redirect(request.path)

        po.status = "SUBMITTED"
        po.save(update_fields=["status", "supplier"])  # 🔑 FIX

        messages.success(request, "Purchase Order submitted for payment.")
        return redirect("inventory:po_detail", pk=po.pk)

    return render(
        request,
        "inventory/po_finalize.html",
        {
            "po": po,
            "items": items,
            "suppliers": Supplier.objects.all(),
        }
    )


@role_required("ACCOUNTANT", "DIRECTOR", "ADMIN")
@transaction.atomic
def po_pay(request, pk):
    hotel = get_user_hotels(request.user)
    po = get_object_or_404(PurchaseOrder, pk=pk, status="SUBMITTED")

    if hotel and po.department.hotel != hotel:
            raise PermissionDenied
    
    if request.method == "POST":
        po.status = "PAID"
        po.paid_by = request.user
        po.paid_at = timezone.now()
        po.save(update_fields=["status", "paid_by", "paid_at"])

        messages.success(request, f"PO #{po.id} marked as PAID.")
        return redirect("inventory:po_detail", pk=po.pk)

    return render(request, "inventory/po_pay.html", {"po": po})



@role_required("STORE")
@transaction.atomic
def po_receive_store(request, pk):

    print(">>> ENTERING po_receive_store VIEW", pk)

    store = request.user.department
    hotel = store.hotel

    po = get_object_or_404(
        PurchaseOrder,
        pk=pk,
        department=store
    )

    # 🔒 Prevent double receiving
    if po.status != "PAID":
        messages.error(request, "This PO cannot be received.")
        return redirect("store_dashboard")

    if request.method == "POST":

        # 1️⃣ Receive goods into STORE
        po.receive(request.user)

        # 2️⃣ If PO originated from a request
        source_request = getattr(po, "source_request", None)

        if source_request:

            destination = source_request.department

            # 🔒 Cross-hotel protection
            if destination.hotel_id != hotel.id:
                raise PermissionDenied("Cross-hotel stock movement blocked.")

            # 🚨 Only transfer if destination is NOT the store
            if destination != store:

                for item in po.items.select_related("product"):

                    transfer_stock(
                        product=item.product,
                        from_department=store,
                        to_department=destination,
                        quantity=item.base_quantity,
                        user=request.user,
                        reference=f"ING-REQ-{source_request.id}"
                    )

            # mark request fulfilled
            source_request.mark_fulfilled()

        messages.success(request, f"PO #{po.id} received successfully.")
        return redirect("store_dashboard")

    return render(
        request,
        "inventory/po_receive_store.html",
        {"po": po}
    )

# =========================
# STORE INBOX
# =========================

@role_required("STORE")
def store_incoming_pos(request):
    pos = PurchaseOrder.objects.filter(
        department=request.user.department,
        status="PAID"
    ).select_related("supplier").order_by("paid_at")

    return render(request, "store/incoming_pos.html", {"pos": pos})


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_stock_requests(request):
    requests = (
        LowStockRequest.objects
        .filter(status="PENDING")
        .select_related("product", "department", "requested_by")
        .order_by("-created_at")
    )

    return render(
        request,
        "inventory/manager/stock_requests.html",
        {"requests": requests}
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def review_stock_request(request, pk):
    req = get_object_or_404(
        LowStockRequest,
        pk=pk,
        status="PENDING"
    )

    if request.method == "POST":
        action = request.POST.get("action")
        approved_qty = int(request.POST.get("approved_quantity", 0))
        note = request.POST.get("manager_note", "").strip()

        # ---- REJECT ----
        if action == "reject":
            req.status = "REJECTED"
            req.manager_note = note
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.save()

            messages.info(request, "Stock request rejected.")
            return redirect("inventory:manager_stock_requests")

        # ---- APPROVE ----
        if action == "approve":
            if approved_qty <= 0:
                messages.error(request, "Approved quantity must be greater than zero.")
                return redirect(request.path)

            if approved_qty > req.requested_quantity:
                messages.error(
                    request,
                    "Approved quantity cannot exceed requested quantity."
                )
                return redirect(request.path)

            # Create DRAFT Purchase Order
            po = PurchaseOrder.objects.create(
                department=req.department,
                created_by=request.user,
                status="DRAFT"
            )

            PurchaseItem.objects.create(
                purchase_order=po,
                product=req.product,
                purchase_quantity=approved_qty,
                unit_cost=0
            )

            # Link request → PO
            req.status = "APPROVED"
            req.manager_note = note
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.purchase_order = po
            req.save()

            messages.success(
                request,
                f"Approved. Draft Purchase Order #{po.id} created."
            )

            return redirect("inventory:po_detail", pk=po.pk)

    return render(
        request,
        "inventory/manager/review_request.html",
        {"request_obj": req}
    )

@role_required("STORE")
def incoming_delivery_detail(request, pk):
    po = get_object_or_404(
        PurchaseOrder,
        pk=pk,
        department=request.user.department,
        status__in=["PAID", "RECEIVED"]
    )

    return render(
        request,
        "store/incoming_delivery_detail.html",
        {"po": po}
    )

@role_required("DIRECTOR")
def product_edit(request, pk):

    product = get_object_or_404(Product, pk=pk)

    form = ProductForm(request.POST or None, instance=product)

    if form.is_valid():

        form.save()

        messages.success(request, "Product updated.")

        return redirect("inventory:product_list")

    return render(
        request,
        "inventory/product_form.html",
        {
            "form": form,
            "product": product
        }
    )

@role_required("DIRECTOR")
def product_delete(request, pk):

    product = get_object_or_404(Product, pk=pk)

    product.is_active = False
    product.save(update_fields=["is_active"])

    messages.success(request, "Product archived.")

    return redirect("inventory:product_list")