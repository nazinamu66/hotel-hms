from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied, ValidationError
from accounts.decorators import role_required
from decimal import Decimal, InvalidOperation
from .services.setup_hotel import setup_new_hotel
from .models import (
    Supplier,
    PurchaseOrder,
    PurchaseItem,
    Department,
    LowStockRequest,
    StockMovement,
    Stock,
    Product,
    HotelFeature,
    Hotel,
)
from .forms import SupplierForm, PurchaseOrderForm, PurchaseItemForm,ProductForm
from .permissions import is_admin, is_manager, is_store
from inventory.models import transfer_stock
from accounting.models import Account
from kitchen.forms import (
    PreparedFoodForm,
    RecipeItemForm,

)
from django.db.models import Q
from accounts.services.access import (
    get_accessible_hotels,
    can_view_product_master,
    can_create_product,
    can_edit_product,
    can_archive_product,
    is_department_head,

)


def sync_product_linen_registration(product, form):
    from linen.models import LinenItem

    register_as_linen = form.cleaned_data.get(
        "register_as_linen",
        False,
    )

    linen_item = (
        LinenItem.objects
        .select_for_update()
        .filter(product=product)
        .first()
    )

    if register_as_linen:
        if product.usage_type != "ASSET":
            raise ValidationError(
                "Only products with usage type ASSET "
                "can be registered as linen."
            )

        linen_item, _ = LinenItem.objects.get_or_create(
            product=product,
            defaults={
                "par_level": form.cleaned_data["linen_par_level"],
                "minimum_level": form.cleaned_data[
                    "linen_minimum_level"
                ],
            },
        )

        linen_item.par_level = form.cleaned_data["linen_par_level"]
        linen_item.minimum_level = form.cleaned_data[
            "linen_minimum_level"
        ]
        linen_item.is_active = True
        linen_item.full_clean()
        linen_item.save()
        return

    if linen_item is not None:
        if linen_item.transactions.exists():
            raise ValidationError(
                "This linen item cannot be unregistered because "
                "it has transaction history. Keep registration "
                "enabled to preserve the linen ledger."
            )

        linen_item.delete()


from restaurant.models import MenuItem


from kitchen.models import (
    Recipe,
    RecipeItem,
)
# =========================
# SUPPLIERS
# =========================

@role_required("ADMIN", "DIRECTOR")
def supplier_list(request):

    accessible_hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
        .order_by("name")
    )

    suppliers = (
        Supplier.objects
        .filter(
            hotel__in=accessible_hotels,
        )
        .select_related("hotel")
        .order_by("hotel__name", "name")
    )

    return render(
        request,
        "inventory/supplier_list.html",
        {
            "suppliers": suppliers,
            "hotels": accessible_hotels,
        },
    )

@role_required("ADMIN", "DIRECTOR")
def supplier_detail(request, pk):

    accessible_hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
    )

    supplier = get_object_or_404(
        Supplier.objects.select_related("hotel"),
        pk=pk,
        hotel__in=accessible_hotels,
    )

    pos = (
        PurchaseOrder.objects
        .filter(
            supplier=supplier,
            status__in=[
                "APPROVED",
                "RECEIVED",
            ],
        )
        .select_related(
            "department",
            "department__hotel",
        )
        .prefetch_related(
            "items__product",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    total_purchases = Decimal("0.00")
    total_paid = Decimal("0.00")
    outstanding_balance = Decimal("0.00")
    partial_orders = 0

    for po in pos:

        po_total = sum(
            (
                item.purchase_quantity * item.unit_cost
                for item in po.items.all()
            ),
            Decimal("0.00"),
        )

        po.calculated_total = po_total

        total_purchases += po_total

        if po.payment_status == "PAID":

            total_paid += po_total

        elif po.payment_status == "UNPAID":

            outstanding_balance += po_total

        elif po.payment_status == "PARTIAL":

            partial_orders += 1

    return render(
        request,
        "inventory/supplier_detail.html",
        {
            "supplier": supplier,
            "pos": pos,
            "total_purchases": total_purchases,
            "total_paid": total_paid,
            "outstanding_balance": outstanding_balance,
            "partial_orders": partial_orders,
        },
    )

@role_required("ADMIN", "DIRECTOR")
def supplier_create(request):

    accessible_hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
        .order_by("name")
    )

    hotel_id = (
        request.POST.get("hotel")
        if request.method == "POST"
        else request.GET.get("hotel")
    )

    hotel = None

    if hotel_id:
        hotel = get_object_or_404(
            accessible_hotels,
            pk=hotel_id,
        )

    elif accessible_hotels.count() == 1:
        hotel = accessible_hotels.first()

    if request.method == "POST" and not hotel:

        messages.error(
            request,
            "Please select a hotel.",
        )

        return redirect(
            request.path,
        )

    form = SupplierForm(
        request.POST or None,
    )

    if form.is_valid():

        supplier = form.save(
            commit=False,
        )

        # ----------------------------------------------------
        # Server-side hotel ownership
        # ----------------------------------------------------

        supplier.hotel = hotel

        supplier.save()

        messages.success(
            request,
            "Supplier created successfully.",
        )

        return redirect(
            "inventory:supplier_list",
        )

    return render(
        request,
        "inventory/supplier_form.html",
        {
            "form": form,
            "hotels": accessible_hotels,
            "selected_hotel": hotel,
        },
    )

@role_required("ADMIN", "DIRECTOR")
def supplier_edit(request, pk):

    accessible_hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
    )

    supplier = get_object_or_404(
        Supplier,
        pk=pk,
        hotel__in=accessible_hotels,
    )

    form = SupplierForm(
        request.POST or None,
        instance=supplier,
    )

    if form.is_valid():

        form.save()

        messages.success(
            request,
            "Supplier updated successfully.",
        )

        return redirect(
            "inventory:supplier_detail",
            pk=supplier.pk,
        )

    return render(
        request,
        "inventory/supplier_form.html",
        {
            "form": form,
            "supplier": supplier,
            "hotels": accessible_hotels,
            "selected_hotel": supplier.hotel,
            "is_edit": True,
        },
    )


@role_required("ADMIN", "DIRECTOR")
@transaction.atomic
def supplier_archive(request, pk):

    if request.method != "POST":
        raise PermissionDenied

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    supplier = get_object_or_404(
        Supplier,
        pk=pk,
        hotel__in=accessible_hotels,
    )

    if supplier.is_active:

        supplier.is_active = False
        supplier.save(
            update_fields=["is_active"]
        )

        messages.success(
            request,
            f"Supplier '{supplier.name}' archived.",
        )

    else:

        supplier.is_active = True
        supplier.save(
            update_fields=["is_active"]
        )

        messages.success(
            request,
            f"Supplier '{supplier.name}' restored.",
        )

    return redirect(
        "inventory:supplier_detail",
        pk=supplier.pk,
    )


def product_list(request):

    if not can_view_product_master(request.user):
        raise PermissionDenied

    product_type = request.GET.get("type")

    accessible_hotels = get_accessible_hotels(
        request.user,
    ).filter(
        is_active=True,
    )

    products = Product.objects.filter(
        hotel__in=accessible_hotels,
        is_active=True,
    )

    # Department heads see only products
    # associated with their department.
    if is_department_head(request.user):
        products = products.filter(
            departments=request.user.department_id,
        )

    if product_type:
        products = products.filter(
            product_type=product_type,
        )

    products = products.order_by(
        "product_type",
        "name",
    )

    context = {
        "products": products,
        "product_types": Product.PRODUCT_TYPE,
        "selected_type": product_type,
        "can_manage_products": request.user.role in {
            "ADMIN",
            "DIRECTOR",
            "GENERAL_MANAGER",
            "MANAGER",
        } or is_department_head(request.user),
    }

    return render(
        request,
        "inventory/product_list.html",
        context
    )

from django.db import IntegrityError



def product_create(request):
    from restaurant.models import MenuItem

    hotels = (
        get_accessible_hotels(request.user)
        .filter(is_active=True)
        .order_by("name")
    )

    if not can_view_product_master(request.user):
        raise PermissionDenied

    hotel_id = (
        request.POST.get("hotel")
        if request.method == "POST"
        else request.GET.get("hotel")
    )

    hotel = None

    if hotel_id:
        hotel = get_object_or_404(hotels, pk=hotel_id)
    elif hotels.count() == 1:
        hotel = hotels.first()

    if request.method == "POST" and not hotel:
        messages.error(request, "Please select a hotel.")
        return redirect(request.path)

    form = ProductForm(
        request.POST or None,
        hotel=hotel,
    )

    if is_department_head(request.user):
        form.fields["departments"].queryset = (
            form.fields["departments"].queryset.filter(
                pk=request.user.department_id,
            )
        )

    if form.is_valid():
        selected_departments = form.cleaned_data.get(
            "departments"
        )

        if not selected_departments:
            form.add_error(
                "departments",
                "At least one department must be selected.",
            )
        else:
            for department in selected_departments:
                if not can_create_product(
                    request.user,
                    department,
                ):
                    raise PermissionDenied

            try:
                with transaction.atomic():
                    product = form.save(commit=False)
                    product.hotel = hotel

                    if product.product_type == "FOOD":
                        product.base_unit = "portion"
                        product.purchase_unit = "portion"
                        product.unit_multiplier = 1
                        product.cost_price = 0
                        product.usage_type = "RESALE"

                    product.save()
                    form.save_m2m()

                    sync_product_linen_registration(
                        product,
                        form,
                    )

                    if product.usage_type == "RESALE":
                        MenuItem.objects.get_or_create(
                            product=product,
                            defaults={
                                "name": product.name,
                                "price": product.price or 0,
                                "is_active": True,
                            },
                        )

            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    "Product created successfully.",
                )
                return redirect("inventory:product_list")

    return render(
        request,
        "inventory/product_form.html",
        {
            "form": form,
            "hotels": hotels,
            "selected_hotel": hotel,
        },
    )


@role_required("ADMIN", "DIRECTOR")
def hotel_feature_setup(request):

    hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
        .order_by(
            "name",
        )
    )

    features = dict(
        HotelFeature.FEATURE_CHOICES
    )

    hotel_id = request.POST.get(
        "hotel",
    ) if request.method == "POST" else request.GET.get(
        "hotel",
    )

    hotel = None

    if hotel_id:
        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

    elif hotels.count() == 1:
        hotel = hotels.first()

    if request.method == "POST":

        if not hotel:
            messages.error(
                request,
                "Please select a hotel.",
            )

            return redirect(
                "inventory:hotel_feature_setup",
            )

        selected = request.POST.getlist(
            "features",
        )

        HotelFeature.objects.filter(
            hotel=hotel,
        ).delete()

        HotelFeature.objects.bulk_create(
            [
                HotelFeature(
                    hotel=hotel,
                    feature=feature,
                )
                for feature in selected
            ]
        )

        messages.success(
            request,
            f"Hotel features updated for {hotel.name}.",
        )

        return redirect(
            f"{request.path}?hotel={hotel.pk}",
        )

    active_features = set()

    if hotel:
        active_features = set(
            HotelFeature.objects.filter(
                hotel=hotel,
                is_active=True,
            ).values_list(
                "feature",
                flat=True,
            )
        )

    return render(
        request,
        "inventory/hotel_features.html",
        {
            "hotels": hotels,
            "hotel": hotel,
            "features": features,
            "active_features": active_features,
        },
    )

# =========================
# HOTEL & DEPARTMENT SETUP
# =========================

@role_required("ADMIN", "DIRECTOR")
def hotel_list(request):

    hotels = (
        get_accessible_hotels(request.user)
        .order_by("name")
    )

    return render(
        request,
        "inventory/hotel_list.html",
        {
            "hotels": hotels,
        },
    )


@role_required("ADMIN", "DIRECTOR")
@transaction.atomic
def hotel_create(request):

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        location = request.POST.get(
            "location",
            "",
        ).strip()

        if not name:
            messages.error(
                request,
                "Hotel name is required.",
            )
            return redirect(request.path)

        if Hotel.objects.filter(
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                "A hotel with this name already exists.",
            )
            return redirect(request.path)

        # -------------------------------------------------
        # Determine organization
        # -------------------------------------------------

        if request.user.role == "DIRECTOR":

            if not request.user.organization_id:
                raise PermissionDenied(
                    "Director is not assigned to an organization."
                )

            organization = request.user.organization

        elif request.user.role == "ADMIN":

            # A platform ADMIN creating a hotel must belong
            # to an organization first.
            if not request.user.organization_id:
                raise PermissionDenied(
                    "Administrator must be assigned to an organization before creating a hotel."
                )

            organization = request.user.organization

        else:
            raise PermissionDenied

        hotel = Hotel.objects.create(
            organization=organization,
            name=name,
            location=location,
        )

        setup_new_hotel(hotel)

        messages.success(
            request,
            f"Hotel '{hotel.name}' created and initialized successfully.",
        )

        return redirect(
            "inventory:hotel_list",
        )

    return render(
        request,
        "inventory/hotel_form.html",
    )


@role_required("ADMIN", "DIRECTOR")
def department_list(request):

    hotels = (
        get_accessible_hotels(request.user)
        .filter(is_active=True)
        .order_by("name")
    )

    departments = Department.objects.filter(
        hotel__in=get_accessible_hotels(request.user),
    )

    # ---------------------------------------------------------
    # FILTERS
    # ---------------------------------------------------------

    search = request.GET.get("search", "").strip()
    hotel_id = request.GET.get("hotel", "").strip()
    department_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        departments = departments.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
        )

    if hotel_id:
        departments = departments.filter(
            hotel_id=hotel_id,
        )

    if department_type:
        departments = departments.filter(
            department_type=department_type,
        )

    if status == "active":
        departments = departments.filter(
            is_active=True,
        )
    elif status == "inactive":
        departments = departments.filter(
            is_active=False,
        )

    departments = (
        departments
        .select_related("hotel")
        .order_by(
            "hotel__name",
            "name",
        )
    )

    return render(
        request,
        "inventory/department_list.html",
        {
            "departments": departments,
            "hotels": hotels,
            "department_types": Department.DEPARTMENT_TYPES,
            "filter_search": search,
            "filter_hotel": hotel_id,
            "filter_type": department_type,
            "filter_status": status,
        },
    )

@role_required("ADMIN", "DIRECTOR")
def department_create(request):

    hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
        .order_by(
            "name",
        )
    )

    valid_types = {
        value
        for value, label in Department.DEPARTMENT_TYPES
    }

    if request.method == "POST":

        hotel_id = request.POST.get(
            "hotel",
        )

        code = request.POST.get(
            "code",
            "",
        ).strip().upper()

        name = request.POST.get(
            "name",
            "",
        ).strip()

        dept_type = request.POST.get(
            "department_type",
            "",
        ).strip()

        is_active = request.POST.get(
            "is_active",
            "on",
        ) == "on"

        if not hotel_id or not code or not name or not dept_type:

            messages.error(
                request,
                "Hotel, department code, name and type are required.",
            )

            return redirect(
                request.path,
            )

        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

        if dept_type not in valid_types:

            messages.error(
                request,
                "Invalid department type selected.",
            )

            return redirect(
                request.path,
            )

        if Department.objects.filter(
            hotel=hotel,
            code=code,
        ).exists():

            messages.error(
                request,
                f"A department with code '{code}' already exists in "
                f"{hotel.name}.",
            )

            return redirect(
                request.path,
            )

        if Department.objects.filter(
            hotel=hotel,
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                f"A department named '{name}' already exists in "
                f"{hotel.name}.",
            )

            return redirect(
                request.path,
            )

        Department.objects.create(
            hotel=hotel,
            code=code,
            name=name,
            department_type=dept_type,
            is_active=is_active,
        )

        messages.success(
            request,
            "Department created successfully.",
        )

        return redirect(
            "inventory:department_list",
        )

    return render(
        request,
        "inventory/department_form.html",
        {
            "hotels": hotels,
            "types": Department.DEPARTMENT_TYPES,
        },
    )


@role_required("ADMIN", "DIRECTOR")
def department_edit(
    request,
    dept_id,
):

    hotels = (
        get_accessible_hotels(
            request.user,
        )
        .filter(
            is_active=True,
        )
        .order_by(
            "name",
        )
    )

    dept = get_object_or_404(
        Department.objects.select_related(
            "hotel",
        ),
        id=dept_id,
        hotel__in=get_accessible_hotels(
            request.user,
        ),
    )

    valid_types = {
        value
        for value, label in Department.DEPARTMENT_TYPES
    }

    if request.method == "POST":

        hotel_id = request.POST.get(
            "hotel",
        )

        code = request.POST.get(
            "code",
            "",
        ).strip().upper()

        name = request.POST.get(
            "name",
            "",
        ).strip()

        dept_type = request.POST.get(
            "department_type",
            "",
        ).strip()

        is_active = request.POST.get(
            "is_active",
        ) == "on"

        if not hotel_id or not code or not name or not dept_type:

            messages.error(
                request,
                "Hotel, department code, name and type are required.",
            )

            return redirect(
                "inventory:department_edit",
                dept_id=dept.pk,
            )

        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

        if dept_type not in valid_types:

            messages.error(
                request,
                "Invalid department type selected.",
            )

            return redirect(
                "inventory:department_edit",
                dept_id=dept.pk,
            )

        if Department.objects.filter(
            hotel=hotel,
            code=code,
        ).exclude(
            pk=dept.pk,
        ).exists():

            messages.error(
                request,
                f"A department with code '{code}' already exists in "
                f"{hotel.name}.",
            )

            return redirect(
                "inventory:department_edit",
                dept_id=dept.pk,
            )

        if Department.objects.filter(
            hotel=hotel,
            name__iexact=name,
        ).exclude(
            pk=dept.pk,
        ).exists():

            messages.error(
                request,
                f"A department named '{name}' already exists in "
                f"{hotel.name}.",
            )

            return redirect(
                "inventory:department_edit",
                dept_id=dept.pk,
            )

        dept.hotel = hotel
        dept.code = code
        dept.name = name
        dept.department_type = dept_type
        dept.is_active = is_active

        dept.save()

        messages.success(
            request,
            "Department updated successfully.",
        )

        return redirect(
            "inventory:department_list",
        )

    return render(
        request,
        "inventory/department_form.html",
        {
            "dept": dept,
            "hotels": hotels,
            "types": Department.DEPARTMENT_TYPES,
            "edit_mode": True,
        },
    )
@role_required("ADMIN", "DIRECTOR")
def department_toggle_active(request, dept_id):

    if request.method != "POST":
        raise PermissionDenied

    department = get_object_or_404(
        Department,
        pk=dept_id,
        hotel__in=get_accessible_hotels(request.user),
    )

    department.is_active = not department.is_active
    department.save(update_fields=["is_active"])

    if department.is_active:
        messages.success(
            request,
            f"Department '{department.name}' has been activated.",
        )
    else:
        messages.success(
            request,
            f"Department '{department.name}' has been made inactive.",
        )

    return redirect("inventory:department_list")

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def recipe_edit(request, food_id):

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied("Only the Head Chef can manage recipes.")

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

@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def recipe_item_delete(request, item_id):

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied("Only the Head Chef can manage recipes.")

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


@role_required("KITCHEN", "MANAGER", "ADMIN", "DIRECTOR")
def recipe_item_add(request, recipe_id):

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied("Only the Head Chef can manage recipes.")

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

@role_required(
    "STORE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
    "ACCOUNTANT",
)
def po_list(request):

    hotels = get_accessible_hotels(request.user)

    qs = (
        PurchaseOrder.objects
        .select_related(
            "supplier",
            "department",
        )
        .order_by("-created_at")
    )

    if request.user.role == "STORE":

        qs = qs.filter(
            department=request.user.department,
            status__in=[
                "APPROVED",
                "RECEIVED",
            ],
        )

    else:

        qs = qs.filter(
            department__hotel__in=hotels,
        )

    return render(
        request,
        "inventory/po_list.html",
        {"pos": qs},
    )

@role_required(
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def po_create(request):

    hotels = get_accessible_hotels(request.user)

    if not hotels.exists():
        raise PermissionDenied(
            "No hotel is associated with this account."
        )

    form = PurchaseOrderForm(
        request.POST or None,
    )

    # Only allow departments belonging to hotels
    # this user is authorized to access.
    form.fields["department"].queryset = (
        Department.objects
        .filter(
            hotel__in=hotels,
            is_active=True,
        )
        .order_by("name")
    )

    if form.is_valid():

        po = form.save(
            commit=False,
        )

        po.created_by = request.user
        po.status = "DRAFT"
        po.payment_status = "UNPAID"

        po.save()

        messages.success(
            request,
            "Draft Purchase Order created.",
        )

        return redirect(
            "inventory:po_detail",
            pk=po.pk,
        )

    return render(
        request,
        "inventory/po_form.html",
        {
            "form": form,
        },
    )


@role_required(
    "STORE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
    "ACCOUNTANT",
    "MAINTENANCE"
)
def po_detail(request, pk):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "supplier",
            "department__hotel",
            "created_by",
            "approved_by",
            "paid_by",
        ),
        pk=pk,
        department__hotel__in=accessible_hotels,
    )

    # --------------------------------------------------------
    # STORE restriction
    # --------------------------------------------------------

    if request.user.role == "STORE":

        if po.department_id != request.user.department_id:
            raise PermissionDenied

        if po.status not in (
            "APPROVED",
            "RECEIVED",
        ):
            raise PermissionDenied

    # --------------------------------------------------------
    # Item form
    # --------------------------------------------------------

    item_form = PurchaseItemForm(
        request.POST or None,
    )

    # --------------------------------------------------------
    # Add item to DRAFT PO
    # --------------------------------------------------------

    if request.method == "POST" and po.status == "DRAFT":

        if request.user.role not in (
            "MANAGER",
            "ADMIN",
            "DIRECTOR",
        ):
            raise PermissionDenied

        if item_form.is_valid():

            item = item_form.save(
                commit=False,
            )

            item.purchase_order = po
            item.save()

            messages.success(
                request,
                "Item added.",
            )

            return redirect(
                "inventory:po_detail",
                pk=po.pk,
            )

    # --------------------------------------------------------
    # Back navigation
    # --------------------------------------------------------

    if request.user.role == "MAINTENANCE":
        back_url = "maintenance_stock_requests"
        back_label = "← Back to Maintenance Stock Requests"

    else:
        back_url = "inventory:po_list"
        back_label = "← Back to Purchase Orders"

        total = sum(
        (
            item.total_cost
            for item in po.items.all()
        ),
        Decimal("0.00"),
    )

    return render(
        request,
        "inventory/po_detail.html",
        {
            "po": po,
            "item_form": item_form,
            "back_url": back_url,
            "back_label": back_label,
            "po_total": total,
        },
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
@require_POST
@transaction.atomic
def po_submit(request, pk):
    hotels = get_accessible_hotels(request.user)

    po = get_object_or_404(
        PurchaseOrder.objects.select_for_update(),
        pk=pk,
        status="DRAFT",
        department__hotel__in=hotels,
    )

    if not po.items.exists():
        messages.error(
            request,
            "Add at least one item.",
        )
        return redirect(
            "inventory:po_detail",
            pk=pk,
        )

    if not po.supplier_id:
        messages.error(
            request,
            "A supplier must be selected before "
            "the Purchase Order can be submitted.",
        )
        return redirect(
            "inventory:po_detail",
            pk=pk,
        )

    # --------------------------------------------------------
    # ADMIN / DIRECTOR
    # Direct approval — no further approval required.
    # --------------------------------------------------------

    if request.user.role in ("ADMIN", "DIRECTOR"):

        po.status = "APPROVED"
        po.approved_by = request.user
        po.approved_at = timezone.now()

        po.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
            ]
        )

        messages.success(
            request,
            f"Purchase Order #{po.id} approved.",
        )

    # --------------------------------------------------------
    # MANAGER
    # Requires Director approval.
    # --------------------------------------------------------

    else:

        po.status = "SUBMITTED"

        po.save(
            update_fields=[
                "status",
            ]
        )

        messages.success(
            request,
            "Purchase Order submitted for Director approval.",
        )

    return redirect(
        "inventory:po_detail",
        pk=pk,
    )

@role_required("DIRECTOR", "ADMIN")
@transaction.atomic
def po_approve(request, pk):

    hotels = get_accessible_hotels(request.user)

    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "supplier",
            "department__hotel",
        ),
        pk=pk,
        status="SUBMITTED",
        department__hotel__in=hotels,
    )

    if not po.supplier_id:
        messages.error(
            request,
            "A supplier is required before approval.",
        )
        return redirect(
            "inventory:po_detail",
            pk=pk,
        )

    if not po.items.exists():
        messages.error(
            request,
            "Purchase Order has no items.",
        )
        return redirect(
            "inventory:po_detail",
            pk=pk,
        )

    if request.method == "POST":

        po.status = "APPROVED"
        po.approved_by = request.user
        po.approved_at = timezone.now()

        po.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
            ]
        )

        messages.success(
            request,
            f"Purchase Order #{po.id} approved.",
        )

        return redirect(
            "inventory:po_detail",
            pk=po.pk,
        )
    
    total = sum(
        (
            item.total_cost
            for item in po.items.all()
        ),
        Decimal("0.00"),
    )

    return render(
        request,
        "inventory/po_approve.html",
        {
            "po": po,
            "po_total": total,

        },
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def po_finalize(request, pk):

    hotels = get_accessible_hotels(request.user)

    po = get_object_or_404(
        PurchaseOrder.objects.select_for_update(),
        pk=pk,
        status="DRAFT",
        department__hotel__in=hotels,
    )

    items = po.items.select_related("product")

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        supplier_id = request.POST.get("supplier")

        if not supplier_id:
            messages.error(
                request,
                "Supplier is required.",
            )
            return redirect(request.path)

        po.supplier = get_object_or_404(
            Supplier,
            id=supplier_id,
            hotel=po.department.hotel,
            is_active=True,
        )

        for item in items:

            qty = int(
                request.POST.get(
                    f"qty_{item.id}",
                    0,
                )
            )

            cost = request.POST.get(
                f"cost_{item.id}",
                "0",
            )

            if qty <= 0:
                item.delete()
            else:
                item.purchase_quantity = qty
                item.unit_cost = cost

                item.save(
                    update_fields=[
                        "purchase_quantity",
                        "unit_cost",
                    ]
                )

        # ----------------------------------------------------
        # ENSURE AT LEAST ONE ITEM REMAINS
        # ----------------------------------------------------

        if not po.items.exists():

            messages.error(
                request,
                "PO must contain at least one item.",
            )

            return redirect(request.path)

        # ----------------------------------------------------
        # FINALIZE
        #
        # ADMIN / DIRECTOR -> AUTO-APPROVED
        # MANAGER -> SUBMITTED FOR DIRECTOR APPROVAL
        # ----------------------------------------------------

        if request.user.role in ("ADMIN", "DIRECTOR"):

            po.status = "APPROVED"
            po.approved_by = request.user
            po.approved_at = timezone.now()

            po.save(
                update_fields=[
                    "supplier",
                    "status",
                    "approved_by",
                    "approved_at",
                ]
            )

            messages.success(
                request,
                f"Purchase Order #{po.id} finalized and approved.",
            )

        else:

            po.status = "SUBMITTED"

            po.save(
                update_fields=[
                    "supplier",
                    "status",
                ]
            )

            messages.success(
                request,
                (
                    "Purchase Order finalized and submitted "
                    "for Director approval."
                ),
            )

        # ----------------------------------------------------
        # REDIRECT AFTER SUCCESS
        # ----------------------------------------------------

        return redirect(
            "inventory:po_detail",
            pk=po.pk,
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "inventory/po_finalize.html",
        {
            "po": po,
            "items": items,
            "suppliers": Supplier.objects.filter(
                hotel=po.department.hotel,
                is_active=True,
            ),
        },
    )


@role_required("ACCOUNTANT", "DIRECTOR", "ADMIN")
@transaction.atomic
def po_pay(request, pk):

    # Payments are now centralized so partial payments and payment history
    # cannot be bypassed through the legacy full-payment screen.
    from accounting.models import SupplierPayment
    if SupplierPayment.objects.filter(source_type="PO", source_id=pk).exists():
        messages.info(request, "This purchase order has payments recorded in Supplier & Vendor Payments. Continue there to avoid duplicate or excess payment.")
        return redirect("accounting:supplier_payments")

    hotels = get_accessible_hotels(request.user)

    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "supplier",
            "department__hotel",
        ),
        pk=pk,
        status__in=["APPROVED", "RECEIVED"],
        department__hotel__in=hotels,
    )
    hotel = po.department.hotel

    total = sum(
        (
            item.purchase_quantity * item.unit_cost
            for item in po.items.all()
        ),
        Decimal("0.00"),
    )

    if request.method == "POST":

        payment_account_id = request.POST.get(
            "payment_account"
        )

        payment_reference = (
            request.POST.get(
                "payment_reference",
                "",
            )
            .strip()
        )

        payment_account = get_object_or_404(
            Account,
            pk=payment_account_id,
            hotel=hotel,
            is_active=True,
            allow_posting=True,
        )

        try:

            from accounting.services.postings.supplier_payment import (
                post_supplier_payment,
            )

            post_supplier_payment(
                po=po,
                payment_account=payment_account,
                amount=total,
                user=request.user,
                reference=(
                    payment_reference
                    or f"PO-PAY-{po.id}"
                ),
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "inventory:po_pay",
                pk=po.pk,
            )

        po.payment_status = "PAID"
        po.paid_by = request.user
        po.paid_at = timezone.now()
        po.payment_reference = payment_reference

        po.save(
            update_fields=[
                "payment_status",
                "paid_by",
                "paid_at",
                "payment_reference",
            ]
        )

        messages.success(
            request,
            f"PO #{po.id} paid successfully.",
        )

        return redirect(
            "inventory:po_detail",
            pk=po.pk,
        )

    payment_accounts = (
        Account.objects
        .filter(
            hotel=hotel,
            account_type__in=[
                "cash",
                "bank",
            ],
            is_active=True,
            allow_posting=True,
        )
        .order_by("code")
    )

    return render(
        request,
        "inventory/po_pay.html",
        {
            "po": po,
            "total": total,
            "payment_accounts": payment_accounts,
        },
    )

@role_required("STORE", "MAINTENANCE", "HOUSEKEEPING", "KITCHEN")
@transaction.atomic
def po_receive(request, pk):

    department = request.user.department

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied(
            "Only the Head Chef can receive Kitchen deliveries."
        )

    if not department:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    po = get_object_or_404(
        PurchaseOrder.objects.select_related(
            "supplier",
            "department__hotel",
        ),
        pk=pk,
        department=department,
        status="APPROVED",
    )

    # ---------------------------------------------------------
    # RECEIVE PURCHASE ORDER
    # ---------------------------------------------------------

    if request.method == "POST":

        try:

            po.receive(
                request.user,
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "inventory:po_receive",
                pk=po.pk,
            )

        messages.success(
            request,
            f"Purchase Order #{po.id} received successfully.",
        )

        # -----------------------------------------------------
        # Department-specific destination
        # -----------------------------------------------------

        if department.department_type == "HOUSEKEEPING":

            return redirect(
                "housekeeping_incoming_pos",
            )

        elif department.department_type == "MAINTENANCE":

            return redirect(
                "maintenance_incoming_pos",
            )

        elif department.department_type == "STORE":

            return redirect(
                "store_dashboard",
            )

        elif department.department_type == "KITCHEN":

            return redirect(
                "kitchen_dashboard",
            )

        raise PermissionDenied(
            "This department does not have a valid "
            "stock receiving destination."
        )

    # ---------------------------------------------------------
    # GET — SHOW RECEIVING PAGE
    # ---------------------------------------------------------

    return render(
        request,
        "inventory/po_receive.html",
        {
            "po": po,
        },
    )
# =========================
# STORE INBOX
# =========================

@role_required("STORE", "MAINTENANCE", "HOUSEKEEPING", "KITCHEN")
def department_incoming_pos(request):

    department = request.user.department

    if request.user.role == "KITCHEN" and not request.user.is_department_head:
        raise PermissionDenied(
            "Only the Head Chef can access Kitchen incoming deliveries."
        )

    if not department:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    pos = (
        PurchaseOrder.objects
        .filter(
            department=department,
            status="APPROVED",
        )
        .select_related("supplier")
        .prefetch_related("items__product")
        .order_by("approved_at", "id")
    )

    if department.department_type == "HOUSEKEEPING":

        back_url = "housekeeping_dashboard"
        back_label = "Back to Housekeeping"

    elif department.department_type == "MAINTENANCE":

        back_url = "maintenance_dashboard"
        back_label = "Back to Maintenance"
    
    elif department.department_type == "KITCHEN":

        back_url = "kitchen_dashboard"
        back_label = "Back to Kitchen"

    elif department.department_type == "STORE":

        back_url = "store_dashboard"
        back_label = "Back to Store"

    else:

        raise PermissionDenied(
            "This department does not use the operational stock receiving workflow."
        )

    return render(
        request,
        "inventory/incoming_pos.html",
        {
            "department": department,
            "pos": pos,
            "back_url": back_url,
            "back_label": back_label,
        },
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
def manager_stock_requests(request):

    hotels = get_accessible_hotels(request.user)

    requests = (
        LowStockRequest.objects
        .filter(
            status="PENDING",
            fulfillment_type="PURCHASE",
            department__hotel__in=hotels,
        )
        .select_related(
            "product",
            "department",
            "requested_by",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "inventory/manager/stock_requests.html",
        {
            "requests": requests,
        },
    )
# =========================
# DEPARTMENT STOCK REQUESTS
# =========================

@role_required("STORE", "MAINTENANCE", "HOUSEKEEPING", "LAUNDRY", "RESTAURANT",
)
def department_request_stock(request):

    department = request.user.department

    if not department:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    if department.department_type not in (
        "RESTAURANT",
        "MAINTENANCE",
        "HOUSEKEEPING",
        "LAUNDRY",
        "STORE"
    ):
        raise PermissionDenied(
            "This department does not use the operational stock request workflow."
        )

    products = (
        Product.objects
        .filter(
            hotel=department.hotel,
            departments=department,
            is_active=True,
        )
        .order_by("name")
        .distinct()
    )

    stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=department
        )
    }

    if request.method == "POST":

        product_id = request.POST.get(
            "product_id",
            "",
        ).strip()

        if not product_id:
            messages.error(
                request,
                "Please select a product.",
            )
            return redirect(request.path)

        try:
            quantity = int(
                request.POST.get(
                    "quantity",
                    0,
                )
            )
        except (TypeError, ValueError):
            quantity = 0

        product = get_object_or_404(
            products,
            pk=product_id,
        )

        if quantity <= 0:
            messages.error(
                request,
                "Quantity must be greater than zero.",
            )
            return redirect(request.path)

        if department.department_type == "RESTAURANT":

            fulfillment_type = "STORE"
        else:
            fulfillment_type = request.POST.get(
                "fulfillment_type",
                ""
            ).strip()

            if fulfillment_type not in (
                "STORE",
                "PURCHASE",
            ):
                messages.error(
                    request,
                    "Please select how this request should be fulfilled.",
                )
                return redirect(request.path)

        LowStockRequest.objects.create(
            product=product,
            department=department,
            requested_quantity=quantity,
            fulfillment_type=fulfillment_type,
            requested_by=request.user,
        )

        messages.success(
            request,
            "Stock request submitted successfully.",
        )

        return redirect(request.path)

    if department.department_type == "HOUSEKEEPING":
        requests_url = "housekeeping_stock_requests"

    elif department.department_type == "MAINTENANCE":
        requests_url = "maintenance_stock_requests"

    elif department.department_type == "LAUNDRY":
        requests_url = "laundry:laundry_stock_requests"

    elif department.department_type == "STORE":
        requests_url = "store_requests"
        
    elif department.department_type == "RESTAURANT":
        requests_url = "restaurant_stock_requests"

    else:
        raise PermissionDenied(
            "This department does not have a valid stock request destination."
        )

    return render(
        request,
        "inventory/department_request_stock.html",
        {
            "department": department,
            "products": products,
            "stock_map": stock_map,
            "requests_url": requests_url,
        },
    )


@role_required("STORE", "MAINTENANCE", "HOUSEKEEPING", "LAUNDRY",  "RESTAURANT",
 )
def department_stock_requests(request):

    department = request.user.department

    if not department:
        raise PermissionDenied(
            "You are not assigned to a department."
        )

    if department.department_type not in (
        "MAINTENANCE",
        "HOUSEKEEPING",
        "LAUNDRY",
        "STORE",
        "RESTAURANT",
        
    ):
        raise PermissionDenied(
            "This department does not use the operational stock request workflow."
        )

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

    order_by = allowed_sorts.get(
        sort,
        "-created_at",
    )

    requests = (
        LowStockRequest.objects
        .filter(
            department=department,
            requested_by=request.user,
        )
        .select_related(
            "product",
            "purchase_order",
        )
        .prefetch_related(
            "transfers",
        )
    )

    if date_from and date_to:
        requests = requests.filter(
            created_at__date__range=[
                date_from,
                date_to,
            ]
        )

    requests = requests.order_by(order_by)
    for req in requests:

        if req.fulfillment_type == "STORE":

            req.total_issued = sum(
                t.quantity
                for t in req.transfers.all()
            )

            req.remaining_quantity = max(
                req.requested_quantity - req.total_issued,
                0,
            )

        else:

            req.total_issued = None
            req.remaining_quantity = None

    if department.department_type == "HOUSEKEEPING":
        request_stock_url = "housekeeping_request_stock"
        back_url = "housekeeping_dashboard"
        back_label = "Back to Housekeeping"
    
    elif department.department_type == "STORE":
        request_stock_url = "store_request_low_stock"
        back_url = "store_dashboard"
        back_label = "Back to Store"

    elif department.department_type == "MAINTENANCE":
        request_stock_url = "maintenance_request_stock"
        back_url = "maintenance_dashboard"
        back_label = "Back to Maintenance"
    
    elif department.department_type == "RESTAURANT":
        request_stock_url = "restaurant_request_stock"
        back_url = "restaurant_pos_v2"
        back_label = "Back to Restaurant"

    elif department.department_type == "LAUNDRY":
        request_stock_url = "laundry:laundry_request_stock"
        back_url = "laundry:dashboard"
        back_label = "Back to Laundry"

    else:
        raise PermissionDenied(
            "This department does not use the operational stock request workflow."
        )

    return render(
        request,
        "inventory/department_stock_requests.html",
        {
            "department": department,
            "requests": requests,
            "date_from": date_from,
            "date_to": date_to,
            "current_sort": sort,
            "request_stock_url": request_stock_url,
            "back_url": back_url,
            "back_label": back_label,
        },
    )

@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def review_stock_request(request, pk):

    hotels = get_accessible_hotels(request.user)

    req = get_object_or_404(
        LowStockRequest,
        pk=pk,
        status="PENDING",
        fulfillment_type="PURCHASE",
        department__hotel__in=hotels,
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



def product_edit(request, pk):
    from restaurant.models import MenuItem

    accessible_hotels = get_accessible_hotels(request.user)

    product = get_object_or_404(
        Product.objects.select_related("hotel"),
        pk=pk,
        hotel__in=accessible_hotels,
    )

    if not can_edit_product(request.user, product):
        raise PermissionDenied

    form = ProductForm(
        request.POST or None,
        instance=product,
        hotel=product.hotel,
    )

    if is_department_head(request.user):
        form.fields["departments"].queryset = (
            form.fields["departments"].queryset.filter(
                pk=request.user.department_id,
            )
        )

    if form.is_valid():
        selected_departments = form.cleaned_data.get(
            "departments"
        )

        if not selected_departments:
            form.add_error(
                "departments",
                "At least one department must be selected.",
            )
        else:
            for department in selected_departments:
                if not can_create_product(
                    request.user,
                    department,
                ):
                    raise PermissionDenied

            try:
                with transaction.atomic():
                    product = form.save(commit=False)

                    if product.product_type == "FOOD":
                        product.base_unit = "portion"
                        product.purchase_unit = "portion"
                        product.unit_multiplier = 1
                        product.cost_price = 0
                        product.usage_type = "RESALE"

                    product.save()
                    form.save_m2m()

                    sync_product_linen_registration(
                        product,
                        form,
                    )

                    if product.usage_type == "RESALE":
                        menu_item, created = (
                            MenuItem.objects.get_or_create(
                                product=product,
                                defaults={
                                    "name": product.name,
                                    "price": product.price or 0,
                                    "is_active": True,
                                },
                            )
                        )

                        if not created:
                            menu_item.name = product.name
                            menu_item.price = product.price or 0
                            menu_item.is_active = True
                            menu_item.save()

            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    "Product updated.",
                )
                return redirect("inventory:product_list")

    return render(
        request,
        "inventory/product_form.html",
        {
            "form": form,
            "product": product,
            "hotels": accessible_hotels,
            "selected_hotel": product.hotel,
        },
    )


@require_POST
def product_delete(request, pk):
    accessible_hotels = get_accessible_hotels(request.user)

    product = get_object_or_404(
        Product,
        pk=pk,
        hotel__in=accessible_hotels,
    )

    if not can_archive_product(
        request.user,
        product,
    ):
        raise PermissionDenied

    product.is_active = False

    product.save(
        update_fields=["is_active"],
    )

    messages.success(
        request,
        "Product archived.",
    )

    return redirect(
        "inventory:product_list",
    )