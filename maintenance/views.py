from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from .services.completion import complete_maintenance_ticket
from accounts.decorators import role_required
from rooms.models import Room
from .models import MaintenanceTicket
from django.core.exceptions import PermissionDenied, ValidationError
from .models import MaintenanceTicket, MaintenanceLabour, MaintenanceMaterial, MaintenanceAsset, MaintenanceSchedule

from datetime import timedelta
from accounts.models import User
from inventory.models import Product, Supplier,Department,Stock,StockMovement,LowStockRequest
from decimal import Decimal, InvalidOperation
from django.db.models import Q
from django.db import transaction
from accounts.services.access import get_accessible_hotels
from inventory.forms import ProductForm,SupplierForm
from accounting.services.postings.maintenance import (
    post_maintenance_labour,
)

from django.db.models import (
    Sum,
    F,
    DecimalField,
    ExpressionWrapper,
)
from .workflows.materials import (
    consume_maintenance_material,
)
from .workflows.preventive import (
    create_preventive_work_order,
)



@role_required(
    "HOUSEKEEPING",
    "FRONTDESK",
    "MAINTENANCE",
    "MANAGER",
    "DIRECTOR",
)
def create_ticket_room(request):

    hotel = request.user.department.hotel

    rooms = (
        Room.objects
        .filter(hotel=hotel)
        .select_related("category")
        .order_by("room_number")
    )

    return render(
        request,
        "maintenance/create_ticket_room.html",
        {
            "rooms": rooms,
        },
    )

@role_required(
    "HOUSEKEEPING",
    "FRONTDESK",
    "MAINTENANCE",
    "MANAGER",
    "DIRECTOR",
)
def create_ticket(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    if request.method == "POST":

        description = request.POST.get(
            "description",
            "",
        ).strip()

        priority = request.POST.get(
            "priority",
            "MEDIUM",
        ).strip()

        if not description:
            messages.error(
                request,
                "Please describe the maintenance issue.",
            )

            return redirect(request.path)

        # ----------------------------------------------------
        # Capture the room state BEFORE Maintenance changes it
        # ----------------------------------------------------

        previous_room_status = room.status

        ticket = MaintenanceTicket.objects.create(
            room=room,
            description=description,
            priority=priority,
            reported_by=request.user,
            room_status_before_maintenance=previous_room_status,
        )

        # ----------------------------------------------------
        # High-priority maintenance takes the room out of order
        # ----------------------------------------------------

        if priority == "HIGH":
            room.status = "OUT_OF_ORDER"

            room.save(
                update_fields=["status"]
            )

        messages.success(
            request,
            "Maintenance ticket created.",
        )

        # ----------------------------------------------------
        # Return user to their own operational area
        # ----------------------------------------------------

        if request.user.role == "MAINTENANCE":
            return redirect(
                "maintenance_dashboard"
            )

        return redirect(
            "housekeeping_dashboard"
        )

    return render(
        request,
        "maintenance/create_ticket.html",
        {
            "room": room,
        },
    )


# ============================================================
# MAINTENANCE DASHBOARD
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_dashboard(request):

    # --------------------------------------------------------
    # Determine hotel scope
    # --------------------------------------------------------

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        hotel_filter = {
            "room__hotel_id": request.user.hotel_id,
        }

    else:

        hotel_filter = {}

    # --------------------------------------------------------
    # Base ticket queryset
    # --------------------------------------------------------

    tickets = (
        MaintenanceTicket.objects
        .filter(**hotel_filter)
    )

    # --------------------------------------------------------
    # Ticket KPIs
    # --------------------------------------------------------

    open_count = tickets.filter(
        status="OPEN",
    ).count()

    in_progress_count = tickets.filter(
        status="IN_PROGRESS",
    ).count()

    high_priority_count = tickets.filter(
        priority="HIGH",
    ).exclude(
        status="RESOLVED",
    ).count()

    # --------------------------------------------------------
    # Current month
    # --------------------------------------------------------

    now = timezone.now()

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    completed_this_month = tickets.filter(
        status="RESOLVED",
        resolved_at__gte=month_start,
    ).count()

    # --------------------------------------------------------
    # Active work orders
    # --------------------------------------------------------

    active_tickets = (
        tickets
        .select_related(
            "room",
            "reported_by",
            "assigned_to",
        )
        .exclude(
            status="RESOLVED",
        )
        .order_by(
            "-priority",
            "-created_at",
        )
    )

    # --------------------------------------------------------
    # Maintenance cost this month
    #
    # Materials:
    #     quantity × unit_cost
    #
    # Labour:
    #     amount
    # --------------------------------------------------------

    material_cost = (
        MaintenanceMaterial.objects
        .filter(
            ticket__in=tickets,
            created_at__gte=month_start,
        )
        .aggregate(
            total=Sum(
                ExpressionWrapper(
                    F("quantity") * F("unit_cost"),
                    output_field=DecimalField(
                        max_digits=14,
                        decimal_places=2,
                    ),
                )
            )
        )["total"]
        or Decimal("0.00")
    )

    labour_cost = (
        MaintenanceLabour.objects
        .filter(
            ticket__in=tickets,
            created_at__gte=month_start,
        )
        .aggregate(
            total=Sum("amount"),
        )["total"]
        or Decimal("0.00")
    )

    total_maintenance_cost = (
        material_cost + labour_cost
    )

    # --------------------------------------------------------
    # Outsourced labour this month
    # --------------------------------------------------------

    outsourced_labour_cost = (
        MaintenanceLabour.objects
        .filter(
            ticket__in=tickets,
            labour_type="OUTSOURCED",
            created_at__gte=month_start,
        )
        .aggregate(
            total=Sum("amount"),
        )["total"]
        or Decimal("0.00")
    )

    # --------------------------------------------------------
    # Preventive Maintenance
    # --------------------------------------------------------

    today = timezone.localdate()

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        preventive_schedules = (
            MaintenanceSchedule.objects
            .select_related(
                "asset",
                "asset__room",
            )
            .filter(
                is_active=True,
                asset__is_active=True,
                asset__hotel_id=request.user.hotel_id,
            )
            .order_by(
                "next_due_date",
                "name",
            )
        )

    else:

        preventive_schedules = (
            MaintenanceSchedule.objects
            .select_related(
                "asset",
                "asset__room",
            )
            .filter(
                is_active=True,
                asset__is_active=True,
            )
            .order_by(
                "next_due_date",
                "name",
            )
        )

    preventive_overdue = preventive_schedules.filter(
        next_due_date__lt=today,
    ).count()

    preventive_due_today = preventive_schedules.filter(
        next_due_date=today,
    ).count()

    preventive_upcoming = preventive_schedules.filter(
        next_due_date__gt=today,
        next_due_date__lte=today + timedelta(days=7),
    ).count()

    preventive_due = preventive_schedules.filter(
        next_due_date__lte=today + timedelta(days=7),
    )[:10]

    # --------------------------------------------------------
    # Render dashboard
    # --------------------------------------------------------

    return render(
        request,
        "maintenance/dashboard.html",
                {
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "high_priority_count": high_priority_count,
            "completed_this_month": completed_this_month,

            "active_tickets": active_tickets,

            "preventive_schedules": preventive_due,
            "preventive_overdue": preventive_overdue,
            "preventive_due_today": preventive_due_today,
            "preventive_upcoming": preventive_upcoming,
            "today": today,

            "material_cost": material_cost,
            "labour_cost": labour_cost,
            "outsourced_labour_cost": outsourced_labour_cost,
            "total_maintenance_cost": total_maintenance_cost,
        },
    )

# ============================================================
# MAINTENANCE WORK ORDER DETAIL
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_ticket_detail(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket.objects
        .select_related(
            "room",
            "reported_by",
            "assigned_to",
        )
        .prefetch_related(
            "work_notes__created_by",
            "labour_entries__technician",
            "labour_entries__supplier",
            "labour_entries__created_by",
            "material_entries__product",
            "material_entries__created_by",
            "material_entries__supplier",
        ),
        id=ticket_id,
    )

    # --------------------------------------------------------
    # Hotel security
    # --------------------------------------------------------

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        if ticket.room.hotel_id != request.user.hotel_id:
            raise PermissionDenied

    # --------------------------------------------------------
    # Add work note
    # --------------------------------------------------------

    if request.method == "POST":

        # Only Maintenance staff can add work notes.
        if request.user.role != "MAINTENANCE":
            raise PermissionDenied

        # Technician must be assigned to this ticket.
        if ticket.assigned_to_id != request.user.id:
            raise PermissionDenied

        note = request.POST.get(
            "note",
            "",
        ).strip()

        if not note:
            messages.warning(
                request,
                "Work note cannot be empty.",
            )

            return redirect(
                "maintenance_ticket_detail",
                ticket_id=ticket.id,
            )

        ticket.work_notes.create(
            created_by=request.user,
            note=note,
        )

        messages.success(
            request,
            "Work note added.",
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    return render(
        request,
        "maintenance/ticket_detail.html",
        {
            "ticket": ticket,
        },
    )

# ============================================================
# TAKE / START TICKET
# ============================================================

@role_required("MAINTENANCE")
def start_ticket(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket,
        id=ticket_id,
        room__hotel_id=request.user.hotel_id,
    )

    if ticket.status != "OPEN":
        messages.warning(
            request,
            "This maintenance ticket is no longer open.",
        )
        return redirect("maintenance_dashboard")

    ticket.assigned_to = request.user
    ticket.status = "IN_PROGRESS"

    ticket.save(
        update_fields=[
            "assigned_to",
            "status",
        ]
    )

    messages.success(
        request,
        f"Maintenance ticket for Room "
        f"{ticket.room.room_number} is now in progress.",
    )

    return redirect("maintenance_dashboard")


# ============================================================
# COMPLETE MAINTENANCE WORK ORDER
# ============================================================

@role_required("MAINTENANCE")
def resolve_ticket(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket,
        id=ticket_id,
        room__hotel_id=request.user.hotel_id,
    )

    if not request.user.hotel_id:
        raise PermissionDenied

    if ticket.assigned_to_id != request.user.id:
        raise PermissionDenied

    if ticket.status != "IN_PROGRESS":
        messages.warning(
            request,
            "Only an in-progress maintenance ticket can be completed.",
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    # ---------------------------------------------------------
    # COST SUMMARY
    # ---------------------------------------------------------

    from decimal import Decimal

    material_total = (
        ticket.material_entries.aggregate(
            total=Sum(
                ExpressionWrapper(
                    F("quantity") * F("unit_cost"),
                    output_field=DecimalField(
                        max_digits=14,
                        decimal_places=4,
                    ),
                )
            )
        )["total"]
        or Decimal("0.00")
    )

    labour_total = (
        ticket.labour_entries.aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )

    total_maintenance_cost = (
        material_total + labour_total
    )

    # ---------------------------------------------------------
    # GET COMPLETION FORM
    # ---------------------------------------------------------

    if request.method != "POST":

        return render(
            request,
            "maintenance/complete_ticket.html",
            {
                "ticket": ticket,
                "material_total": material_total,
                "labour_total": labour_total,
                "total_maintenance_cost": total_maintenance_cost,
            },
        )

    # ---------------------------------------------------------
    # POST
    # ---------------------------------------------------------

    outcome = request.POST.get(
        "completion_outcome",
        "",
    ).strip()

    resolution_note = request.POST.get(
        "resolution_note",
        "",
    ).strip()

    try:

        complete_maintenance_ticket(
            ticket,
            outcome=outcome,
            resolution_note=resolution_note,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return render(
            request,
            "maintenance/complete_ticket.html",
            {
                "ticket": ticket,
                "material_total": material_total,
                "labour_total": labour_total,
                "total_maintenance_cost": total_maintenance_cost,
            },
        )

    messages.success(
        request,
        "Maintenance work order completed successfully.",
    )

    return redirect(
        "maintenance_dashboard",
    )

# ============================================================
# ADD MAINTENANCE LABOUR
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
@transaction.atomic
def add_labour(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket,
        id=ticket_id,
    )

    # --------------------------------------------------------
    # Hotel security
    # --------------------------------------------------------

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        if ticket.room.hotel_id != request.user.hotel_id:
            raise PermissionDenied

    # --------------------------------------------------------
    # Only active work orders may receive labour
    # --------------------------------------------------------

    if ticket.status != "IN_PROGRESS":

        messages.warning(
            request,
            "Labour can only be added to an in-progress work order.",
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    if request.method == "POST":

        labour_type = request.POST.get(
            "labour_type",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        amount = request.POST.get(
            "amount",
            "0",
        ).strip()

        technician_id = request.POST.get(
            "technician",
        )

        supplier_id = request.POST.get(
            "supplier",
        )

        # ----------------------------------------------------
        # Basic validation
        # ----------------------------------------------------

        if labour_type not in (
            "INTERNAL",
            "OUTSOURCED",
        ):

            messages.error(
                request,
                "Invalid labour type.",
            )

            return redirect(request.path)

        if not description:

            messages.error(
                request,
                "A description of the labour is required.",
            )

            return redirect(request.path)

        try:
            amount = Decimal(amount)

            if amount < 0:
                raise InvalidOperation

        except (InvalidOperation, TypeError):

            messages.error(
                request,
                "Please enter a valid labour amount.",
            )

            return redirect(request.path)


        # ----------------------------------------------------
        # Internal labour
        # ----------------------------------------------------

        technician = None
        supplier = None

        if labour_type == "INTERNAL":

            if not technician_id:

                messages.error(
                    request,
                    "Please select the technician.",
                )

                return redirect(request.path)

            technician = get_object_or_404(
                User,
                id=technician_id,
                role="MAINTENANCE",
            )

            if technician.hotel_id != ticket.room.hotel_id:
                raise PermissionDenied

        # ----------------------------------------------------
        # Outsourced labour
        # ----------------------------------------------------

        elif labour_type == "OUTSOURCED":

            if not supplier_id:

                messages.error(
                    request,
                    "Please select a supplier.",
                )

                return redirect(request.path)

            supplier = get_object_or_404(
                Supplier,
                id=supplier_id,
                hotel=ticket.room.hotel,
            )

        # ----------------------------------------------------
        # Create labour record
        # ----------------------------------------------------

        labour = MaintenanceLabour.objects.create(
            ticket=ticket,
            labour_type=labour_type,
            technician=technician,
            supplier=supplier,
            description=description,
            amount=amount,
            created_by=request.user,
        )

        if labour_type == "OUTSOURCED":
            post_maintenance_labour(
                labour=labour,
                user=request.user,
            )

        messages.success(
            request,
            "Maintenance labour added successfully.",
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    # --------------------------------------------------------
    # Form data
    # --------------------------------------------------------

    technicians = User.objects.filter(
        role="MAINTENANCE",
        hotel_id=ticket.room.hotel_id,
        is_active=True,
    ).order_by(
        "username",
    )

    suppliers = Supplier.objects.filter(
        hotel=ticket.room.hotel,
    ).order_by(
        "name",
    )

    return render(
        request,
        "maintenance/add_labour.html",
        {
            "ticket": ticket,
            "technicians": technicians,
            "suppliers": suppliers,
        },
    )

# ============================================================
# ADD MAINTENANCE MATERIAL
# ============================================================

@role_required("MAINTENANCE")
def add_material(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket,
        id=ticket_id,
    )

    # --------------------------------------------------------
    # Hotel security
    # --------------------------------------------------------

    if not request.user.hotel_id:
        raise PermissionDenied

    if ticket.room.hotel_id != request.user.hotel_id:
        raise PermissionDenied

    # --------------------------------------------------------
    # Technician security
    # --------------------------------------------------------

    if ticket.assigned_to_id != request.user.id:
        raise PermissionDenied

    if ticket.status != "IN_PROGRESS":

        messages.warning(
            request,
            "Materials can only be added to "
            "an in-progress work order.",
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    # --------------------------------------------------------
    # Maintenance department
    # --------------------------------------------------------

    maintenance_department = get_object_or_404(
        Department,
        hotel_id=ticket.room.hotel_id,
        code="MNT",
        department_type="MAINTENANCE",
        is_active=True,
    )

    # --------------------------------------------------------
    # Products authorized for Maintenance
    # --------------------------------------------------------

    products = (
        Product.objects
        .filter(
            is_active=True,
            usage_type="INTERNAL",
            departments=maintenance_department,
        )
        .order_by("name")
        .distinct()
    )

    # --------------------------------------------------------
    # Build product + stock rows
    #
    # Stock quantity is already stored in BASE UNITS.
    # --------------------------------------------------------

    product_rows = []

    for product in products:

        stock = (
            Stock.objects
            .filter(
                product=product,
                department=maintenance_department,
            )
            .first()
        )

        product_rows.append(
            {
                "product": product,
                "stock": stock.quantity if stock else 0,
            }
        )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        product_id = request.POST.get(
            "product",
        )

        quantity = request.POST.get(
            "quantity",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        product = get_object_or_404(
            Product,
            id=product_id,
            is_active=True,
            usage_type="INTERNAL",
            departments=maintenance_department,
        )

        try:

            from decimal import Decimal

            quantity = Decimal(quantity)

            material = consume_maintenance_material(
                ticket=ticket,
                product=product,
                quantity=quantity,
                user=request.user,
                description=description,
            )

        except (
            ValidationError,
            ValueError,
            TypeError,
        ) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        messages.success(
            request,
            (
                f"{material.product.name} "
                f"({material.quantity} "
                f"{material.product.base_unit}) "
                "was consumed from Maintenance Store."
            ),
        )

        return redirect(
            "maintenance_ticket_detail",
            ticket_id=ticket.id,
        )

    # --------------------------------------------------------
    # Render
    # --------------------------------------------------------

    return render(
        request,
        "maintenance/add_material.html",
        {
            "ticket": ticket,
            "product_rows": product_rows,
        },
    )
# ============================================================
# MAINTENANCE ASSETS
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_assets(request):

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        assets = (
            MaintenanceAsset.objects
            .select_related(
                "hotel",
                "room",
            )
            .filter(
                hotel_id=request.user.hotel_id,
            )
            .order_by(
                "name",
            )
        )

    else:

        assets = (
            MaintenanceAsset.objects
            .select_related(
                "hotel",
                "room",
            )
            .order_by(
                "hotel",
                "name",
            )
        )

    return render(
        request,
        "maintenance/assets.html",
        {
            "assets": assets,
        },
    )


@role_required(
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_asset_create(request):

    if request.user.role == "MANAGER":

        if not request.user.hotel_id:
            raise PermissionDenied

        hotel = request.user.hotel

    elif request.user.role in (
        "ADMIN",
        "DIRECTOR",
    ):

        # For now, these roles use their assigned hotel
        # when creating a maintenance asset.
        if not request.user.hotel_id:
            raise PermissionDenied

        hotel = request.user.hotel

    else:

        raise PermissionDenied

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        asset_type = request.POST.get(
            "asset_type",
            "",
        ).strip()

        serial_number = request.POST.get(
            "serial_number",
            "",
        ).strip()

        manufacturer = request.POST.get(
            "manufacturer",
            "",
        ).strip()

        model_number = request.POST.get(
            "model_number",
            "",
        ).strip()

        room_id = request.POST.get(
            "room",
            "",
        ).strip()

        if not name:

            messages.error(
                request,
                "Asset name is required.",
            )

            return redirect(request.path)

        room = None

        if room_id:

            room = get_object_or_404(
                Room,
                id=room_id,
                hotel=hotel,
            )

        MaintenanceAsset.objects.create(
            hotel=hotel,
            room=room,
            name=name,
            asset_type=asset_type,
            serial_number=serial_number,
            manufacturer=manufacturer,
            model_number=model_number,
        )

        messages.success(
            request,
            "Maintenance asset created successfully.",
        )

        return redirect(
            "maintenance_assets",
        )

    rooms = (
        Room.objects
        .filter(
            hotel=hotel,
        )
        .order_by(
            "room_number",
        )
    )

    return render(
        request,
        "maintenance/asset_form.html",
        {
            "rooms": rooms,
        },
    )

# ============================================================
# PREVENTIVE MAINTENANCE SCHEDULES
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_schedules(request):

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        schedules = (
            MaintenanceSchedule.objects
            .select_related(
                "asset",
                "asset__room",
            )
            .filter(
                asset__hotel_id=request.user.hotel_id,
                asset__is_active=True,
            )
            .order_by(
                "next_due_date",
                "name",
            )
        )

    else:

        schedules = (
            MaintenanceSchedule.objects
            .select_related(
                "asset",
                "asset__room",
            )
            .filter(
                asset__is_active=True,
            )
            .order_by(
                "next_due_date",
                "name",
            )
        )

    today = timezone.localdate()

    for schedule in schedules:

        if schedule.next_due_date < today:
            schedule.schedule_status = "OVERDUE"

        elif schedule.next_due_date == today:
            schedule.schedule_status = "DUE TODAY"

        else:
            schedule.schedule_status = "SCHEDULED"

    return render(
        request,
        "maintenance/schedules.html",
        {
            "schedules": schedules,
            "today": today,
        },
    )


@role_required(
    "MANAGER",
    "MAINTENANCE",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_schedule_create(request):

    if request.user.role == "MAINTENANCE":

        if not request.user.is_department_head:
            raise PermissionDenied

        if not request.user.hotel_id:
            raise PermissionDenied

    if not request.user.hotel_id:
        raise PermissionDenied

    hotel = request.user.hotel

    assets = (
        MaintenanceAsset.objects
        .filter(
            hotel=hotel,
            is_active=True,
        )
        .select_related(
            "room",
        )
        .order_by(
            "name",
        )
    )

    if request.method == "POST":

        asset_id = request.POST.get(
            "asset",
            "",
        ).strip()

        name = request.POST.get(
            "name",
            "",
        ).strip()

        interval_days = request.POST.get(
            "interval_days",
            "",
        ).strip()

        next_due_date = request.POST.get(
            "next_due_date",
            "",
        ).strip()

        if not asset_id:

            messages.error(
                request,
                "An asset is required.",
            )

            return redirect(request.path)

        if not name:

            messages.error(
                request,
                "Schedule name is required.",
            )

            return redirect(request.path)

        try:

            interval_days = int(
                interval_days,
            )

            if interval_days <= 0:
                raise ValueError

        except (TypeError, ValueError):

            messages.error(
                request,
                "Interval must be a positive number of days.",
            )

            return redirect(request.path)

        if not next_due_date:

            messages.error(
                request,
                "Next due date is required.",
            )

            return redirect(request.path)

        asset = get_object_or_404(
            MaintenanceAsset,
            id=asset_id,
            hotel=hotel,
            is_active=True,
        )

        MaintenanceSchedule.objects.create(
            asset=asset,
            name=name,
            interval_days=interval_days,
            next_due_date=next_due_date,
        )

        messages.success(
            request,
            "Preventive maintenance schedule created successfully.",
        )

        return redirect(
            "maintenance_schedules",
        )

    return render(
        request,
        "maintenance/schedule_form.html",
        {
            "assets": assets,
        },
    )
@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_schedule_work_order(
    request,
    schedule_id,
):

    schedule = get_object_or_404(
        MaintenanceSchedule.objects
        .select_related(
            "asset",
            "asset__room",
        ),
        id=schedule_id,
    )

    # ---------------------------------------------------------
    # Hotel security
    # ---------------------------------------------------------

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        if schedule.asset.hotel_id != request.user.hotel_id:
            raise PermissionDenied

    else:

        if not request.user.hotel_id:
            raise PermissionDenied

        if schedule.asset.hotel_id != request.user.hotel_id:
            raise PermissionDenied

    # ---------------------------------------------------------
    # Generate work order
    # ---------------------------------------------------------

    try:

        ticket = create_preventive_work_order(
            schedule=schedule,
            user=request.user,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "maintenance_schedules",
        )

    # ---------------------------------------------------------
    # Existing work order?
    # ---------------------------------------------------------

    if ticket.schedule_id == schedule.id:

        messages.success(
            request,
            (
                f"Preventive work order #{ticket.id} "
                f"is ready."
            ),
        )

    return redirect(
        "maintenance_ticket_detail",
        ticket_id=ticket.id,
    )

@role_required("MAINTENANCE")
def request_maintenance_stock(request):

    if not request.user.hotel_id:
        raise PermissionDenied(
            "You are not assigned to a hotel."
        )

    maintenance_department = get_object_or_404(
        Department,
        hotel_id=request.user.hotel_id,
        code="MNT",
        department_type="MAINTENANCE",
        is_active=True,
    )

    products = (
        Product.objects
        .filter(
            departments=maintenance_department,
            is_active=True,
            usage_type="INTERNAL",
        )
        .order_by("name")
        .distinct()
    )

    stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=maintenance_department
        )
    }

    for product in products:
        product.current_stock = stock_map.get(
            product.id,
            0,
        )

    if request.method == "POST":

        product_id = request.POST.get("product_id")

        try:
            quantity = int(
                request.POST.get("quantity", 0)
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

            return redirect(
                "maintenance_request_stock"
            )

        LowStockRequest.objects.create(
            product=product,
            department=maintenance_department,
            requested_quantity=quantity,
            requested_by=request.user,
        )

        messages.success(
            request,
            "Maintenance stock request submitted.",
        )

        return redirect(
            "maintenance_my_stock_requests"
        )

    return render(
        request,
        "maintenance/request_stock.html",
        {
            "maintenance_department": maintenance_department,
            "products": products,
            "stock_map": stock_map,
        },
    )


@role_required("MAINTENANCE")
def my_maintenance_stock_requests(request):

    requests = (
        LowStockRequest.objects
        .filter(
            department__hotel_id=request.user.hotel_id,
            department__code="MNT",
            requested_by=request.user,
        )
        .select_related(
            "product",
            "department",
            "purchase_order",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "maintenance/my_stock_requests.html",
        {
            "requests": requests,
        },
    )

# ============================================================
# MAINTENANCE WORK ORDERS
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_work_orders(request):

    # --------------------------------------------------------
    # Base hotel scope
    # --------------------------------------------------------

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        tickets = MaintenanceTicket.objects.filter(
            room__hotel_id=request.user.hotel_id,
        )

    else:

        tickets = MaintenanceTicket.objects.all()

    # --------------------------------------------------------
    # Only active work orders
    # --------------------------------------------------------

    tickets = tickets.exclude(
        status="RESOLVED",
    )

    # --------------------------------------------------------
    # Filters
    # --------------------------------------------------------

    status = request.GET.get(
        "status",
        "",
    ).strip()

    priority = request.GET.get(
        "priority",
        "",
    ).strip()

    assigned = request.GET.get(
        "assigned",
        "",
    ).strip()

    room = request.GET.get(
        "room",
        "",
    ).strip()

    # --------------------------------------------------------
    # Status filter
    # --------------------------------------------------------

    if status in (
        "OPEN",
        "IN_PROGRESS",
    ):
        tickets = tickets.filter(
            status=status,
        )

    # --------------------------------------------------------
    # Priority filter
    # --------------------------------------------------------

    if priority in (
        "LOW",
        "MEDIUM",
        "HIGH",
    ):
        tickets = tickets.filter(
            priority=priority,
        )

    # --------------------------------------------------------
    # Assignment filter
    # --------------------------------------------------------

    if assigned == "ME":

        if request.user.role != "MAINTENANCE":
            raise PermissionDenied

        tickets = tickets.filter(
            assigned_to=request.user,
        )

    elif assigned == "UNASSIGNED":

        tickets = tickets.filter(
            assigned_to__isnull=True,
        )

    # --------------------------------------------------------
    # Room filter
    # --------------------------------------------------------

    if room:

        tickets = tickets.filter(
            room__room_number__icontains=room,
        )

    # --------------------------------------------------------
    # Final queryset
    # --------------------------------------------------------

    tickets = (
        tickets
        .select_related(
            "room",
            "reported_by",
            "assigned_to",
        )
        .order_by(
            "-created_at",
        )
    )

    return render(
        request,
        "maintenance/work_orders.html",
        {
            "tickets": tickets,
            "page_title": "Maintenance Work Orders",

            "status_filter": status,
            "priority_filter": priority,
            "assigned_filter": assigned,
            "room_filter": room,
        },
    )

# ============================================================
# MY WORK ORDERS
# ============================================================

@role_required("MAINTENANCE")
def my_work_orders(request):

    if not request.user.hotel_id:
        raise PermissionDenied

    tickets = (
        MaintenanceTicket.objects
        .select_related(
            "room",
            "reported_by",
            "assigned_to",
        )
        .filter(
            room__hotel_id=request.user.hotel_id,
            assigned_to=request.user,
        )
        .exclude(
            status="RESOLVED",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "maintenance/work_orders.html",
        {
            "tickets": tickets,
            "page_title": "My Work Orders",
            "my_work_orders": True,
        },
    )


# ============================================================
# MAINTENANCE WORK HISTORY
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_work_history(request):

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        tickets = (
            MaintenanceTicket.objects
            .select_related(
                "room",
                "reported_by",
                "assigned_to",
            )
            .prefetch_related(
                "labour_entries",
                "material_entries",
            )
            .filter(
                room__hotel_id=request.user.hotel_id,
                status="RESOLVED",
            )
            .order_by("-resolved_at", "-id")
        )

    else:

        tickets = (
            MaintenanceTicket.objects
            .select_related(
                "room",
                "reported_by",
                "assigned_to",
            )
            .prefetch_related(
                "labour_entries",
                "material_entries",
            )
            .filter(
                status="RESOLVED",
            )
            .order_by("-resolved_at", "-id")
        )

    # --------------------------------------------------------
    # Calculate historical costs
    #
    # MaintenanceMaterial stores:
    #     quantity
    #     unit_cost
    #
    # Therefore:
    #     material cost = quantity × unit cost
    # --------------------------------------------------------

    for ticket in tickets:

        material_cost = sum(
            (
                material.quantity * material.unit_cost
                for material in ticket.material_entries.all()
            ),
            Decimal("0.00"),
        )

        labour_cost = sum(
            (
                labour.amount
                for labour in ticket.labour_entries.all()
            ),
            Decimal("0.00"),
        )

        ticket.history_material_cost = material_cost
        ticket.history_labour_cost = labour_cost
        ticket.history_total_cost = (
            material_cost + labour_cost
        )

    return render(
        request,
        "maintenance/work_history.html",
        {
            "tickets": tickets,
        },
    )

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_stock(request):

    # --------------------------------------------------------
    # Accessible hotels
    # --------------------------------------------------------

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    # --------------------------------------------------------
    # Maintenance departments within accessible hotels
    # --------------------------------------------------------

    maintenance_departments = (
        Department.objects
        .filter(
            hotel__in=accessible_hotels,
            code="MNT",
            department_type="MAINTENANCE",
            is_active=True,
        )
        .select_related("hotel")
    )

    # --------------------------------------------------------
    # Maintenance stock
    # --------------------------------------------------------

    stock_items = (
        Stock.objects
        .select_related(
            "product",
            "department",
            "department__hotel",
        )
        .filter(
            department__in=maintenance_departments,
            product__is_active=True,
        )
        .order_by(
            "department__hotel__name",
            "product__name",
        )
    )

    # --------------------------------------------------------
    # Calculate stock information
    # --------------------------------------------------------

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
        "maintenance/stock.html",
        {
            "maintenance_departments": maintenance_departments,
            "stock_items": stock_items,
            "accessible_hotels": accessible_hotels,
        },
    )

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_stock_movements(request):

    # --------------------------------------------------------
    # Accessible hotels
    # --------------------------------------------------------

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    # --------------------------------------------------------
    # Maintenance departments within accessible hotels
    # --------------------------------------------------------

    maintenance_departments = (
        Department.objects
        .filter(
            hotel__in=accessible_hotels,
            code="MNT",
            department_type="MAINTENANCE",
            is_active=True,
        )
        .select_related("hotel")
    )

    # --------------------------------------------------------
    # Stock movements involving Maintenance
    # --------------------------------------------------------

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
                from_department__in=maintenance_departments,
            )
            |
            Q(
                to_department__in=maintenance_departments,
            )
        )
        .filter(
            product__is_active=True,
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

    movements = movements.order_by(
        order_by,
        "-id",
    )

    return render(
        request,
        "maintenance/stock_movements.html",
        {
            "maintenance_departments": maintenance_departments,
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
# MAINTENANCE PRODUCTS
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_products(request):

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        products = (
            Product.objects
            .filter(
                hotel_id=request.user.hotel_id,
                is_active=True,
                usage_type="INTERNAL",
                departments__code="MNT",
            )
            .distinct()
            .order_by("name")
        )

    else:

        products = (
            Product.objects
            .filter(
                is_active=True,
                usage_type="INTERNAL",
                departments__code="MNT",
            )
            .select_related("hotel")
            .distinct()
            .order_by(
                "hotel__name",
                "name",
            )
        )

    return render(
        request,
        "maintenance/products.html",
        {
            "products": products,
        },
    )
@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_product_create(request):

    if not request.user.hotel_id:
        raise PermissionDenied

    hotel = request.user.hotel

    maintenance_department = get_object_or_404(
        Department,
        hotel=hotel,
        code="MNT",
        department_type="MAINTENANCE",
        is_active=True,
    )

    form = ProductForm(
        request.POST or None,
        hotel=hotel,
        maintenance_mode=True,
    )

    if form.is_valid():

        product = form.save(commit=False)

        # ----------------------------------------------------
        # SECURITY / OWNERSHIP
        # ----------------------------------------------------

        product.hotel = hotel

        # ----------------------------------------------------
        # Maintenance products are INTERNAL
        # ----------------------------------------------------

        product.usage_type = "INTERNAL"

        product.save()

        # ----------------------------------------------------
        # Only Maintenance may use this product
        # ----------------------------------------------------

        form.save_m2m()

        product.departments.set(
            [maintenance_department]
        )

        messages.success(
            request,
            "Maintenance product created successfully.",
        )

        return redirect(
            "maintenance_products",
        )

    return render(
        request,
        "maintenance/product_form.html",
        {
            "form": form,
            "hotel": hotel,
            "maintenance_department": maintenance_department,
        },
    )

# ============================================================
# MAINTENANCE SUPPLIERS
# ============================================================

@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_suppliers(request):

    if request.user.role in (
        "MAINTENANCE",
        "MANAGER",
    ):

        if not request.user.hotel_id:
            raise PermissionDenied

        suppliers = (
            Supplier.objects
            .filter(
                hotel_id=request.user.hotel_id,
            )
            .order_by("name")
        )

    else:

        accessible_hotels = get_accessible_hotels(
            request.user,
        )

        suppliers = (
            Supplier.objects
            .filter(
                hotel__in=accessible_hotels,
            )
            .select_related("hotel")
            .order_by(
                "hotel__name",
                "name",
            )
        )

    return render(
        request,
        "maintenance/suppliers.html",
        {
            "suppliers": suppliers,
        },
    )
@role_required(
    "MAINTENANCE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def maintenance_supplier_create(request):

    if not request.user.hotel_id:
        raise PermissionDenied(
            "You are not assigned to a hotel."
        )

    hotel = request.user.hotel

    form = SupplierForm(
        request.POST or None,
    )

    if form.is_valid():

        supplier = form.save(
            commit=False,
        )

        # ----------------------------------------------------
        # SECURITY
        # ----------------------------------------------------

        supplier.hotel = hotel
        supplier.save()

        messages.success(
            request,
            "Maintenance supplier created successfully.",
        )

        return redirect(
            "maintenance_suppliers",
        )

    return render(
        request,
        "maintenance/supplier_form.html",
        {
            "form": form,
            "hotel": hotel,
        },
    )