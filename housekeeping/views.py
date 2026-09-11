from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Prefetch, Sum
from rooms.models import Room
from django.contrib import messages
from django.db.models import Q
from .models import (
    CleaningAssignment,
    CleaningLog,
    CleaningMaterialUsage,
    LostFoundItem,
)
from linen.services.workflows import (
    
    return_linen_to_laundry,
)

from accounts.models import User
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError

from linen.models import (
    LinenItem,
    LinenRequest,
    LinenTransaction,
)
from linen.services.balances import get_linen_summary
from inventory.models import Product, Stock,Department,StockMovement

from accounts.services.access import (
    user_can_access_hotel,
    get_accessible_hotels,
    can_manage_department,
)
from housekeeping.workflows.materials import consume_cleaning_material

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def dashboard(request):

    # ---------------------------------------------------------
    # HOTEL ACCESS
    # ---------------------------------------------------------

    hotels = get_accessible_hotels(request.user)

    today = timezone.localdate()

    # ---------------------------------------------------------
    # ROOM COUNTS
    # ---------------------------------------------------------

    dirty_rooms = (
        Room.objects
        .filter(
            hotel__in=hotels,
            status__in=[
                "VACANT_DIRTY",
                "OCCUPIED_DIRTY",
            ],
        )
        .select_related("hotel")
        .order_by(
            "hotel__name",
            "room_number",
        )
    )

    dirty_room_count = dirty_rooms.count()

    # ---------------------------------------------------------
    # ACTIVE CLEANING ASSIGNMENTS
    # ---------------------------------------------------------

    active_assignments = (
        CleaningAssignment.objects
        .filter(
            room__hotel__in=hotels,
            status__in=[
                "ASSIGNED",
                "IN_PROGRESS",
                "INSPECTION",
            ],
        )
        .select_related(
            "room",
            "room__hotel",
            "assigned_to",
            "assigned_by",
            "inspected_by",
        )
        .order_by(
            "room__hotel__name",
            "room__room_number",
        )
    )

    assigned_count = active_assignments.filter(
        status="ASSIGNED"
    ).count()

    in_progress_count = active_assignments.filter(
        status="IN_PROGRESS"
    ).count()

    inspection_count = active_assignments.filter(
        status="INSPECTION"
    ).count()

    # ---------------------------------------------------------
    # TODAY'S CLEANING ACTIVITY
    # ---------------------------------------------------------

    cleaned_today = (
        CleaningLog.objects
        .filter(
            room__hotel__in=hotels,
            cleaned_at__date=today,
        )
        .select_related(
            "room",
            "room__hotel",
            "cleaned_by",
        )
        .order_by(
            "-cleaned_at",
        )
    )

    cleaned_today_count = cleaned_today.count()

    # ---------------------------------------------------------
    # TODAY'S MATERIAL USAGE
    # ---------------------------------------------------------

    material_usage_today = (
        CleaningMaterialUsage.objects
        .filter(
            assignment__room__hotel__in=hotels,
            recorded_at__date=today,
        )
        .select_related(
            "assignment",
            "assignment__room",
            "product",
            "recorded_by",
        )
        .order_by(
            "-recorded_at",
        )
    )

    materials_used_today = (
        material_usage_today
        .values(
            "product__name",
            "product__base_unit",
        )
        .annotate(
            total_quantity=Sum("quantity"),
        )
        .order_by(
            "product__name",
        )
    )

    # ---------------------------------------------------------
    # HOUSEKEEPING STOCK
    # ---------------------------------------------------------

    housekeeping_departments = (
        request.user.department
        if request.user.role == "HOUSEKEEPING"
        else None
    )

    if housekeeping_departments:
        housekeeping_stock = (
            Stock.objects
            .select_related(
                "product",
                "department",
                "department__hotel",
            )
            .filter(
                department=housekeeping_departments,
                product__is_active=True,
                product__usage_type="INTERNAL",
            )
            .order_by(
                "product__name",
            )
        )
    else:
        housekeeping_stock = (
            Stock.objects
            .select_related(
                "product",
                "department",
                "department__hotel",
            )
            .filter(
                department__hotel__in=hotels,
                department__department_type="HOUSEKEEPING",
                department__is_active=True,
                product__is_active=True,
                product__usage_type="INTERNAL",
            )
            .order_by(
                "department__hotel__name",
                "product__name",
            )
        )

    low_stock_items = [
        stock
        for stock in housekeeping_stock
        if stock.quantity <= stock.product.reorder_level
    ]

    # ---------------------------------------------------------
    # LOST & FOUND
    # ---------------------------------------------------------

    lost_found_open = (
        LostFoundItem.objects
        .filter(
            hotel__in=hotels,
            status="FOUND",
        )
        .select_related(
            "hotel",
            "room",
            "found_by",
        )
        .order_by(
            "-found_at",
        )
    )

    lost_found_count = lost_found_open.count()

    lost_found_today_count = (
        LostFoundItem.objects
        .filter(
            hotel__in=hotels,
            found_at__date=today,
        )
        .count()
    )

    # ---------------------------------------------------------
    # RENDER
    # ---------------------------------------------------------

    return render(
        request,
        "housekeeping/dashboard.html",
        {
            "dirty_rooms": dirty_rooms,
            "dirty_room_count": dirty_room_count,

            "active_assignments": active_assignments,
            "assigned_count": assigned_count,
            "in_progress_count": in_progress_count,
            "inspection_count": inspection_count,

            "cleaned_today": cleaned_today,
            "cleaned_today_count": cleaned_today_count,

            "materials_used_today": materials_used_today,
            "material_usage_today": material_usage_today,

            "housekeeping_stock": housekeeping_stock,
            "low_stock_items": low_stock_items,

            "lost_found_open": lost_found_open,
            "lost_found_count": lost_found_count,
            "lost_found_today_count": lost_found_today_count,
        },
    )

from housekeeping.models import CleaningLog


@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def cleaning_history(request):

    hotels = get_accessible_hotels(request.user)

    logs = (
        CleaningLog.objects
        .select_related(
            "room",
            "room__hotel",
            "cleaned_by",
        )
        .filter(
            room__hotel__in=hotels,
        )
        .order_by(
            "-cleaned_at",
        )[:100]
    )

    return render(
        request,
        "housekeeping/history.html",
        {
            "logs": logs,
        },
    )

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)

def assign_room(request, room_id):

    from housekeeping.workflows.assign_room import (
        assign_room as assign_room_workflow,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
    )

    if not user_can_access_hotel(
        request.user,
        room.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's housekeeping operations."
        )

    from inventory.models import Department

    housekeeping_department = get_object_or_404(
        Department,
        hotel=room.hotel,
        department_type="HOUSEKEEPING",
        is_active=True,
    )

    if not can_manage_department(
        request.user,
        housekeeping_department,
    ):
        raise PermissionDenied(
            "You do not have permission to manage Housekeeping assignments."
        )

    housekeepers = User.objects.filter(
        role="HOUSEKEEPING",
        department=housekeeping_department,
        is_active=True,
    ).order_by(
        "username",
    )

    if request.method == "POST":

        try:

            assign_room_workflow(
                room=room,
                assigned_by=request.user,
                housekeeper_id=request.POST.get("user"),
            )

        except PermissionDenied as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                request.path,
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                request.path,
            )

        messages.success(
            request,
            f"Room {room.room_number} assigned.",
        )

        return redirect(
            "housekeeping_dashboard",
        )

    return render(
        request,
        "housekeeping/assign_room.html",
        {
            "room": room,
            "housekeepers": housekeepers,
        },
    )

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def lost_found_list(request):

    from accounts.services.access import (
        get_accessible_hotels,
    )

    hotels = get_accessible_hotels(
        request.user,
    )

    items = (
        LostFoundItem.objects
        .select_related(
            "hotel",
            "room",
            "found_by",
        )
        .filter(
            hotel__in=hotels,
        )
        .order_by(
            "-found_at",
        )
    )

    return render(
        request,
        "housekeeping/lost_found_list.html",
        {
            "items": items,
        },
    )



@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def lost_found_create(request):

    from housekeeping.workflows.lost_found import (
        record_item,
    )

    from accounts.services.access import (
        get_accessible_hotels,
    )

    hotels = get_accessible_hotels(
        request.user,
    )

    rooms = (
        Room.objects
        .filter(
            hotel__in=hotels,
        )
        .select_related(
            "hotel",
        )
        .order_by(
            "hotel__name",
            "room_number",
        )
    )

    if request.method == "POST":

        hotel_id = request.POST.get("hotel")
        room_id = request.POST.get("room")

        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

        room = None

        if room_id:
            room = get_object_or_404(
                Room,
                pk=room_id,
                hotel=hotel,
            )

        try:

            record_item(
                hotel=hotel,
                room=room,
                description=request.POST.get(
                    "description",
                ),
                found_by=request.user,
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                request.path,
            )

        messages.success(
            request,
            "Item recorded successfully.",
        )

        return redirect(
            "housekeeping_lost_found",
        )

    return render(
        request,
        "housekeeping/lost_found_create.html",
        {
            "hotels": hotels,
            "rooms": rooms,
        },
    )
@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def lost_found_claim(request, item_id):

    from housekeeping.workflows.lost_found import (
        claim_item,
    )

    hotels = get_accessible_hotels(
        request.user,
    )

    item = get_object_or_404(
        LostFoundItem.objects.select_related(
            "hotel",
            "room",
            "found_by",
        ),
        pk=item_id,
        hotel__in=hotels,
    )

    if request.method == "POST":

        try:

            claim_item(
                item=item,
                user=request.user,
                claimant=request.POST.get(
                    "claimant",
                ),
            )

        except (
            ValidationError,
            PermissionDenied,
        ) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "housekeeping_lost_found",
            )

        messages.success(
            request,
            "Lost & Found item marked as claimed.",
        )

        return redirect(
            "housekeeping_lost_found",
        )

    return render(
        request,
        "housekeeping/lost_found_claim.html",
        {
            "item": item,
        },
    )
@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def lost_found_dispose(request, item_id):

    from housekeeping.workflows.lost_found import (
        dispose_item,
    )

    hotels = get_accessible_hotels(
        request.user,
    )

    item = get_object_or_404(
        LostFoundItem.objects.select_related(
            "hotel",
            "room",
            "found_by",
        ),
        pk=item_id,
        hotel__in=hotels,
    )

    if request.method != "POST":

        return redirect(
            "housekeeping_lost_found",
        )

    try:

        dispose_item(
            item=item,
            user=request.user,
        )

    except (
        ValidationError,
        PermissionDenied,
    ) as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_lost_found",
        )

    messages.success(
        request,
        "Lost & Found item marked as disposed.",
    )

    return redirect(
        "housekeeping_lost_found",
    )


@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
)
def start_cleaning(request, room_id):

    from housekeeping.workflows.start_cleaning import (
        start_cleaning as start_cleaning_workflow,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
    )

    if not user_can_access_hotel(
        request.user,
        room.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's housekeeping operations."
        )

    try:

        start_cleaning_workflow(
            room=room,
            user=request.user,
        )

    except (ValidationError, PermissionDenied) as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_dashboard",
        )

    messages.success(
        request,
        f"Cleaning started for Room {room.room_number}.",
    )

    return redirect(
        "housekeeping_dashboard",
    )

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
)
def finish_cleaning(request, room_id):

    from housekeeping.workflows.finish_cleaning import (
        finish_cleaning as finish_cleaning_workflow,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
    )

    if not user_can_access_hotel(
        request.user,
        room.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's housekeeping operations."
        )

    try:

        finish_cleaning_workflow(
            room=room,
            user=request.user,
        )

    except (ValidationError, PermissionDenied) as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_dashboard",
        )

    messages.success(
        request,
        f"Room {room.room_number} is waiting for inspection.",
    )

    return redirect(
        "housekeeping_dashboard",
    )

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def approve_cleaning(request, room_id):

    from housekeeping.workflows.approve_cleaning import (
        approve_cleaning as approve_cleaning_workflow,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
    )

    if not user_can_access_hotel(
        request.user,
        room.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's housekeeping operations."
        )

    try:

        approve_cleaning_workflow(
            room=room,
            user=request.user,
        )

    except (ValidationError, PermissionDenied) as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_dashboard",
        )

    messages.success(
        request,
        f"Room {room.room_number} approved and ready.",
    )

    return redirect(
        "housekeeping_dashboard",
    )

@role_required(
    "HOUSEKEEPING",
)
def cleaning_material_add(request, assignment_id):

    assignment = get_object_or_404(
        CleaningAssignment.objects.select_related(
            "room",
            "room__hotel",
            "assigned_to",
        ),
        pk=assignment_id,
    )

    # ---------------------------------------------------------
    # Security
    # ---------------------------------------------------------

    if assignment.assigned_to_id != request.user.id:
        raise PermissionDenied(
            "You can only record materials for your own "
            "cleaning assignments."
        )

    # ---------------------------------------------------------
    # Assignment status
    # ---------------------------------------------------------

    if assignment.status not in (
        "ASSIGNED",
        "IN_PROGRESS",
    ):
        messages.error(
            request,
            "Materials can only be recorded for an active "
            "cleaning assignment.",
        )

        return redirect(
            "housekeeping_dashboard",
        )

    # ---------------------------------------------------------
    # Housekeeping department
    # ---------------------------------------------------------

    housekeeping_department = (
        assignment.room.hotel.departments
        .filter(
            department_type="HOUSEKEEPING",
            is_active=True,
        )
        .first()
    )

    if not housekeeping_department:
        raise PermissionDenied(
            "No active Housekeeping department exists "
            "for this hotel."
        )

    # ---------------------------------------------------------
    # Authorized Housekeeping products
    # ---------------------------------------------------------

    products = (
        Product.objects
        .filter(
            is_active=True,
            usage_type="INTERNAL",
            departments=housekeeping_department,
        )
        .order_by("name")
        .distinct()
    )

    # ---------------------------------------------------------
    # POST — record material
    # ---------------------------------------------------------

    if request.method == "POST":

        product_id = (
            request.POST.get(
                "product_id",
                "",
            )
            .strip()
        )

        quantity_raw = (
            request.POST.get(
                "quantity",
                "",
            )
            .strip()
        )

        notes = (
            request.POST.get(
                "notes",
                "",
            )
            .strip()
        )

        # -----------------------------------------------------
        # Product selection
        # -----------------------------------------------------

        if not product_id:

            messages.error(
                request,
                "Please select a Housekeeping material.",
            )

            return redirect(
                request.path,
            )

        try:

            product = products.get(
                pk=product_id,
            )

        except (
            Product.DoesNotExist,
            ValueError,
            TypeError,
        ):

            messages.error(
                request,
                "Invalid Housekeeping material selected.",
            )

            return redirect(
                request.path,
            )

        # -----------------------------------------------------
        # Quantity
        # -----------------------------------------------------

        if not quantity_raw:

            messages.error(
                request,
                "Please enter a quantity.",
            )

            return redirect(
                request.path,
            )

        try:

            quantity = Decimal(
                quantity_raw,
            )

        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Enter a valid quantity.",
            )

            return redirect(
                request.path,
            )

        if quantity <= 0:

            messages.error(
                request,
                "Quantity must be greater than zero.",
            )

            return redirect(
                request.path,
            )

        # -----------------------------------------------------
        # Consume material
        # -----------------------------------------------------

        try:

            usage = consume_cleaning_material(
                assignment=assignment,
                product=product,
                quantity=quantity,
                user=request.user,
                notes=notes,
            )

        except (
            ValidationError,
            PermissionDenied,
        ) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                request.path,
            )

        # -----------------------------------------------------
        # Success
        # -----------------------------------------------------

        messages.success(
            request,
            (
                f"{usage.product.name} "
                f"({usage.quantity} "
                f"{usage.product.base_unit}) "
                "recorded successfully."
            ),
        )

        return redirect(
            request.path,
        )

    # ---------------------------------------------------------
    # GET — display material form/history
    # ---------------------------------------------------------

    usages = (
        assignment.material_usages
        .select_related(
            "product",
            "recorded_by",
        )
        .order_by(
            "-recorded_at",
        )
    )

    return render(
        request,
        "housekeeping/cleaning_materials.html",
        {
            "assignment": assignment,
            "products": products,
            "usages": usages,
        },
    )


@role_required(
    "HOUSEKEEPING",
)
def cleaning_linen_check(request, assignment_id):

    from housekeeping.workflows.linen_check import (
        get_assignment,
        get_or_create_linen_checks,
        save_linen_checks,
    )

    assignment = get_assignment(
        assignment_id,
    )

    # ---------------------------------------------------------
    # SECURITY
    # ---------------------------------------------------------

    if assignment.assigned_to_id != request.user.id:
        raise PermissionDenied(
            "You can only manage the linen checklist "
            "for your own cleaning assignment."
        )

    if not user_can_access_hotel(
        request.user,
        assignment.room.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's "
            "housekeeping operations."
        )

    # ---------------------------------------------------------
    # OPEN CHECKLIST
    # ---------------------------------------------------------

    try:

        checks = get_or_create_linen_checks(
            assignment=assignment,
            user=request.user,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_dashboard",
        )

    # ---------------------------------------------------------
    # SAVE CHECKLIST
    # ---------------------------------------------------------

    if request.method == "POST":

        submitted_checks = {}

        for check in checks:

            item_id = str(
                check.linen_item_id,
            )

            submitted_checks[item_id] = {
                "found_quantity": request.POST.get(
                    f"found_{item_id}",
                    "0",
                ),

                "clean_issued_quantity": request.POST.get(
                    f"clean_{item_id}",
                    "0",
                ),

                "dirty_collected_quantity": request.POST.get(
                    f"dirty_{item_id}",
                    "0",
                ),

                "final_quantity": request.POST.get(
                    f"final_{item_id}",
                    "0",
                ),

                "note": request.POST.get(
                    f"note_{item_id}",
                    "",
                ),
            }

        try:

            save_linen_checks(
                assignment=assignment,
                user=request.user,
                checks=submitted_checks,
            )

        except (ValidationError, PermissionDenied) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "housekeeping_cleaning_linen",
                assignment_id=assignment.id,
            )

        messages.success(
            request,
            "Linen checklist saved successfully.",
        )

        return redirect(
            "housekeeping_dashboard",
        )

    return render(
        request,
        "housekeeping/cleaning_linen_check.html",
        {
            "assignment": assignment,
            "checks": checks,
        },
    )


@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def housekeeping_stock(request):

    # ---------------------------------------------------------
    # Accessible hotels
    # ---------------------------------------------------------

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    # ---------------------------------------------------------
    # Housekeeping departments
    #
    # HOUSEKEEPING:
    #     Own department only.
    #
    # MANAGEMENT:
    #     All active Housekeeping departments within
    #     accessible hotels.
    # ---------------------------------------------------------

    if request.user.role == "HOUSEKEEPING":

        department = request.user.department

        if not department:
            raise PermissionDenied(
                "You are not assigned to a department."
            )

        if department.department_type != "HOUSEKEEPING":
            raise PermissionDenied(
                "Your department is not Housekeeping."
            )

        housekeeping_departments = (
            Department.objects
            .filter(
                pk=department.pk,
                department_type="HOUSEKEEPING",
                is_active=True,
            )
            .select_related("hotel")
        )

    else:

        housekeeping_departments = (
            Department.objects
            .filter(
                hotel__in=accessible_hotels,
                department_type="HOUSEKEEPING",
                is_active=True,
            )
            .select_related("hotel")
        )

    # ---------------------------------------------------------
    # Housekeeping stock
    # ---------------------------------------------------------

    stock_items = (
        Stock.objects
        .select_related(
            "product",
            "department",
            "department__hotel",
        )
        .filter(
            department__in=housekeeping_departments,
            product__is_active=True,
            product__usage_type="INTERNAL",
        )
        .order_by(
            "department__hotel__name",
            "product__name",
        )
    )

    # ---------------------------------------------------------
    # Calculate stock information
    # ---------------------------------------------------------

    for stock in stock_items:

        stock.stock_value = (
            stock.quantity
            * stock.product.cost_price
        )

        if stock.quantity <= 0:

            stock.stock_status = "OUT OF STOCK"

        elif stock.quantity <= stock.product.reorder_level:

            stock.stock_status = "LOW STOCK"

        else:

            stock.stock_status = "OK"

    return render(
        request,
        "housekeeping/stock.html",
        {
            "housekeeping_departments": housekeeping_departments,
            "stock_items": stock_items,
            "accessible_hotels": accessible_hotels,
        },
    )

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def housekeeping_stock_movements(request):

    # ---------------------------------------------------------
    # Accessible hotels
    # ---------------------------------------------------------

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    # ---------------------------------------------------------
    # Housekeeping departments
    #
    # HOUSEKEEPING:
    #     Own department only.
    #
    # MANAGEMENT:
    #     All active Housekeeping departments within
    #     accessible hotels.
    # ---------------------------------------------------------

    if request.user.role == "HOUSEKEEPING":

        department = request.user.department

        if not department:
            raise PermissionDenied(
                "You are not assigned to a department."
            )

        if department.department_type != "HOUSEKEEPING":
            raise PermissionDenied(
                "Your department is not Housekeeping."
            )

        housekeeping_departments = (
            Department.objects
            .filter(
                pk=department.pk,
                department_type="HOUSEKEEPING",
                is_active=True,
            )
            .select_related("hotel")
        )

    else:

        housekeeping_departments = (
            Department.objects
            .filter(
                hotel__in=accessible_hotels,
                department_type="HOUSEKEEPING",
                is_active=True,
            )
            .select_related("hotel")
        )

        # ---------------------------------------------------------
    # Stock movements involving Housekeeping
    #
    # IN:
    #     Housekeeping is destination.
    #
    # OUT:
    #     Housekeeping is source.
    #
    # TRANSFER:
    #     Housekeeping is either source or destination.
    # ---------------------------------------------------------

    movements = (
        StockMovement.objects
        .select_related(
            "product",
            "from_department",
            "from_department__hotel",
            "to_department",
            "to_department__hotel",
            "created_by",
        )
        .filter(
            Q(
                from_department__in=housekeeping_departments,
            )
            |
            Q(
                to_department__in=housekeeping_departments,
            )
        )
        .filter(
            product__is_active=True,
        )
    )

    # ---------------------------------------------------------
    # SEARCH
    # ---------------------------------------------------------

    search = request.GET.get(
        "search",
        "",
    ).strip()

    if search:

        search_filter = (
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

        movements = movements.filter(
            search_filter
        )

    # ---------------------------------------------------------
    # DATE FILTER
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # MOVEMENT TYPE
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # SORTING
    # ---------------------------------------------------------

    sort = request.GET.get(
        "sort",
        "-date",
    )

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

    movements = movements.order_by(
        order_by,
        "-id",
    )

    return render(
        request,
        "housekeeping/stock_movements.html",
        {
            "housekeeping_departments": housekeeping_departments,
            "movements": movements,
            "accessible_hotels": accessible_hotels,

            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "movement_type": movement_type,
            "current_sort": sort,
        },
    )

# ============================================================
# REQUEST NEW LINEN
# ============================================================

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def request_new_linen(request):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=accessible_hotels,
            is_active=True,
            product__is_active=True,
        )
        .select_related(
            "product",
            "product__hotel",
        )
        .order_by(
            "product__name",
        )
    )

    if request.method == "POST":

        linen_item_id = request.POST.get(
            "linen_item",
        )

        quantity_raw = request.POST.get(
            "quantity",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        if not linen_item_id:

            messages.error(
                request,
                "Please select a linen item.",
            )

        elif not quantity_raw:

            messages.error(
                request,
                "Please enter a quantity.",
            )

        else:

            try:
                quantity = int(
                    quantity_raw,
                )

            except (TypeError, ValueError):

                quantity = 0

            if quantity <= 0:

                messages.error(
                    request,
                    "Quantity must be greater than zero.",
                )

            else:

                linen_item = get_object_or_404(
                    linen_items,
                    pk=linen_item_id,
                )

                LinenRequest.objects.create(
                    linen_item=linen_item,
                    quantity=quantity,
                    request_type="STORE",
                    requested_by=request.user,
                    note=note,
                )

                messages.success(
                    request,
                    f"Request for {quantity} "
                    f"{linen_item.product.name} "
                    "submitted to the Linen Store.",
                )

                return redirect(
                    "housekeeping_linen_requests",
                )

    return render(
        request,
        "housekeeping/linen_request.html",
        {
            "linen_items": linen_items,
        },
    )

# ============================================================
# REQUEST CLEAN LINEN FROM LAUNDRY
# ============================================================

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def request_laundry_linen(request):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=accessible_hotels,
            is_active=True,
            product__is_active=True,
        )
        .select_related(
            "product",
            "product__hotel",
        )
        .order_by(
            "product__name",
        )
    )

    if request.method == "POST":

        linen_item_id = request.POST.get(
            "linen_item",
        )

        quantity_raw = request.POST.get(
            "quantity",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        if not linen_item_id:

            messages.error(
                request,
                "Please select a linen item.",
            )

        elif not quantity_raw:

            messages.error(
                request,
                "Please enter a quantity.",
            )

        else:

            try:
                quantity = int(
                    quantity_raw,
                )

            except (TypeError, ValueError):

                quantity = 0

            if quantity <= 0:

                messages.error(
                    request,
                    "Quantity must be greater than zero.",
                )

            else:

                linen_item = get_object_or_404(
                    linen_items,
                    pk=linen_item_id,
                )

                LinenRequest.objects.create(
                    linen_item=linen_item,
                    quantity=quantity,
                    request_type="LAUNDRY",
                    requested_by=request.user,
                    note=note,
                )

                messages.success(
                    request,
                    f"Request for {quantity} "
                    f"{linen_item.product.name} "
                    "submitted to Laundry.",
                )

                return redirect(
                    "housekeeping_linen_requests",
                )

    return render(
        request,
        "housekeeping/linen_request_laundry.html",
        {
            "linen_items": linen_items,
        },
    )
# ============================================================
# MY LINEN REQUESTS
# ============================================================

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def housekeeping_linen_requests(request):

    requests = (
        LinenRequest.objects
        .filter(
            requested_by=request.user,
            request_type__in=["STORE", "LAUNDRY"],
        )
        .select_related(
            "linen_item",
            "linen_item__product",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    for linen_request in requests:

        linen_request.remaining_quantity = (
            linen_request.quantity
            - linen_request.fulfilled_quantity
        )

    return render(
        request,
        "housekeeping/linen_requests.html",
        {
            "linen_requests": requests,
        },
    )

# ============================================================
# SEND LINEN TO LAUNDRY
# ============================================================

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def send_linen_to_laundry(request):

    hotels = get_accessible_hotels(
        request.user,
    )

    if not hotels.exists():
        raise PermissionDenied

    from linen.models import LinenCustody

    custodies = (
        LinenCustody.objects
        .filter(
            user=request.user,
            linen_item__is_active=True,
            linen_item__product__is_active=True,
            linen_item__product__hotel__in=hotels,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
        )
        .order_by(
            "linen_item__product__name",
        )
    )

    if request.method == "POST":

        reference = request.POST.get(
            "reference",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        returned_any = False

        for custody in custodies:

            item_id = custody.linen_item_id

            clean_raw = request.POST.get(
                f"clean_quantity_{item_id}",
                "0",
            ).strip()

            dirty_raw = request.POST.get(
                f"dirty_quantity_{item_id}",
                "0",
            ).strip()

            try:

                clean_quantity = int(
                    clean_raw or 0
                )

                dirty_quantity = int(
                    dirty_raw or 0
                )

            except (TypeError, ValueError):

                messages.error(
                    request,
                    f"{custody.linen_item.product.name}: "
                    "quantities must be whole numbers.",
                )

                return redirect(
                    request.path,
                )

            if clean_quantity < 0:

                messages.error(
                    request,
                    f"{custody.linen_item.product.name}: "
                    "clean quantity cannot be negative.",
                )

                return redirect(
                    request.path,
                )

            if dirty_quantity < 0:

                messages.error(
                    request,
                    f"{custody.linen_item.product.name}: "
                    "dirty quantity cannot be negative.",
                )

                return redirect(
                    request.path,
                )

            if (
                clean_quantity == 0
                and dirty_quantity == 0
            ):
                continue

            try:

                return_linen_to_laundry(
                    linen_item=custody.linen_item,
                    user=request.user,
                    clean_quantity=clean_quantity,
                    dirty_quantity=dirty_quantity,
                    reference=reference,
                    note=note,
                )

            except ValidationError as e:

                messages.error(
                    request,
                    str(e),
                )

                return redirect(
                    request.path,
                )

            returned_any = True

        if not returned_any:

            messages.error(
                request,
                "Enter a quantity of clean or dirty linen to return.",
            )

            return redirect(
                request.path,
            )

        messages.success(
            request,
            "Linen returned to Laundry successfully.",
        )

        return redirect(
            request.path,
        )

    return render(
        request,
        "housekeeping/send_linen_to_laundry.html",
        {
            "custodies": custodies,
        },
    )

# ============================================================
# LINEN MOVEMENT HISTORY
# ============================================================

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def housekeeping_linen_movements(request):

    hotels = get_accessible_hotels(request.user)

    if not hotels.exists():
        raise PermissionDenied

    movements = (
        LinenTransaction.objects
        .filter(
            linen_item__product__hotel__in=hotels,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
            "performed_by",
        )
        .order_by(
            "-created_at",
            "-id",
        )
    )

    return render(
        request,
        "housekeeping/linen_movements.html",
        {
            "movements": movements,
        },
    )