from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from .services.completion import complete_maintenance_ticket
from accounts.decorators import role_required
from rooms.models import Room
from .models import MaintenanceTicket
from django.core.exceptions import PermissionDenied, ValidationError
from .models import MaintenanceTicket, MaintenanceLabour, MaintenanceMaterial, MaintenanceAsset, MaintenanceSchedule, MaintenanceScheduleCycle, MaintenanceLocation, MaintenanceDirectPurchaseRequest

from datetime import timedelta, date
from accounts.models import User
from inventory.models import Product, Supplier,Department,Stock,StockMovement,LowStockRequest,PurchaseOrder,PurchaseItem
from decimal import Decimal, InvalidOperation
from django.db.models import Q
from django.db import transaction
from django.views.decorators.http import require_POST
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
    "HOUSEKEEPING", "FRONTDESK", "MAINTENANCE", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT", "RESTAURANT", "STORE", "KITCHEN", "LAUNDRY", "GYM", "BOUTIQUE", "SECURITY",
)
def create_ticket_room(request):
    """Show only the room and non-room reporting options allowed for this role."""
    hotels = get_accessible_hotels(request.user)
    role = (request.user.role or "").upper()
    department_type = getattr(getattr(request.user, "department", None), "department_type", "") or role or role
    allowed_types = {
        "LAUNDRY": {"LAUNDRY"}, "KITCHEN": {"KITCHEN"}, "RESTAURANT": {"RESTAURANT"},
        "GYM": {"GYM"}, "STORE": {"STORE"}, "BOUTIQUE": {"OTHER"},
        "SECURITY": {"GROUNDS", "PARKING", "GATE", "CORRIDOR", "STAIRWELL", "PUBLIC_AREA"},
        "HOUSEKEEPING": {"CORRIDOR", "STAIRWELL", "LOBBY", "PUBLIC_AREA"},
        "FRONTDESK": {"RECEPTION", "LOBBY", "PUBLIC_AREA"},
    }
    privileged = role in {"MAINTENANCE", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT"}
    rooms = Room.objects.filter(hotel__in=hotels).select_related("hotel", "category").order_by("hotel__name", "room_number")
    locations = MaintenanceLocation.objects.filter(hotel__in=hotels, is_active=True, status="ACTIVE").select_related("hotel").prefetch_related("responsible_departments")
    if not privileged:
        allowed = allowed_types.get(role, set()) | allowed_types.get(department_type, set())
        locations = locations.filter(location_type__in=allowed)
        if department_type:
            locations = locations.filter(Q(responsible_departments__isnull=True) | Q(responsible_departments__department_type=department_type)).distinct()
        else:
            locations = locations.filter(responsible_departments__isnull=True)
        if role in {"LAUNDRY", "KITCHEN", "RESTAURANT", "GYM", "STORE", "SECURITY"}:
            rooms = rooms.none()
    return render(request, "maintenance/create_ticket_room.html", {"rooms": rooms, "locations": locations.order_by("hotel__name", "name"), "hotels": hotels})


@role_required(
    "HOUSEKEEPING", "FRONTDESK", "MAINTENANCE", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT", "RESTAURANT", "STORE", "KITCHEN", "LAUNDRY", "GYM", "BOUTIQUE", "SECURITY",
)
def create_ticket(request, room_id):
    hotels = get_accessible_hotels(request.user)
    room = get_object_or_404(Room.objects.select_related("hotel", "category"), pk=room_id, hotel__in=hotels)
    if request.user.role in {"LAUNDRY", "KITCHEN", "RESTAURANT", "GYM", "STORE", "SECURITY"}:
        raise PermissionDenied
    assets = MaintenanceAsset.objects.filter(hotel=room.hotel, room=room, is_active=True).order_by("name")
    if request.method == "POST":
        asset_id = request.POST.get("asset", "").strip()
        asset = get_object_or_404(assets, pk=asset_id) if asset_id else None
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "MEDIUM").strip().upper()
        valid_priorities = {choice[0] for choice in MaintenanceTicket.PRIORITY_CHOICES}
        if not description:
            messages.error(request, "Please describe the maintenance issue.")
            return redirect(request.path)
        if priority not in valid_priorities:
            messages.error(request, "Please select a valid priority.")
            return redirect(request.path)
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room.pk)
            ticket = MaintenanceTicket.objects.create(
                room=room,
                asset=asset,
                description=description,
                priority=priority,
                reported_by=request.user,
                room_status_before_maintenance=room.status,
            )
            if priority == "HIGH":
                room.status = "OUT_OF_ORDER"
                room.save(update_fields=["status"])
        messages.success(request, f"Maintenance issue for Room {room.room_number} reported successfully (ticket #{ticket.pk}).")
        if request.user.role == "MAINTENANCE":
            return redirect("maintenance_dashboard")
        return redirect("maintenance_create_ticket_room")
    return render(request, "maintenance/create_ticket.html", {"room": room, "assets": assets})


@role_required(
    "HOUSEKEEPING", "FRONTDESK", "MAINTENANCE", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT", "RESTAURANT", "STORE", "KITCHEN", "LAUNDRY", "GYM", "BOUTIQUE", "SECURITY",
)
def create_ticket_location(request, location_id):
    hotels = get_accessible_hotels(request.user)
    location = get_object_or_404(MaintenanceLocation.objects.select_related("hotel"), pk=location_id, hotel__in=hotels, is_active=True, status="ACTIVE")
    role = (request.user.role or "").upper()
    department_type = getattr(getattr(request.user, "department", None), "department_type", "")
    allowed_types = {
        "LAUNDRY": {"LAUNDRY"}, "KITCHEN": {"KITCHEN"}, "RESTAURANT": {"RESTAURANT"}, "GYM": {"GYM"}, "STORE": {"STORE"},
        "SECURITY": {"GROUNDS", "PARKING", "GATE", "CORRIDOR", "STAIRWELL", "PUBLIC_AREA"},
        "HOUSEKEEPING": {"CORRIDOR", "STAIRWELL", "LOBBY", "PUBLIC_AREA"}, "FRONTDESK": {"RECEPTION", "LOBBY", "PUBLIC_AREA"},
    }
    privileged = role in {"MAINTENANCE", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT"}
    allowed = allowed_types.get(role, set()) | allowed_types.get(department_type, set())
    configured = set(location.responsible_departments.values_list("department_type", flat=True))
    if not privileged and (location.location_type not in allowed or (configured and department_type not in configured)):
        raise PermissionDenied
    assets = MaintenanceAsset.objects.filter(hotel=location.hotel, location=location, is_active=True).order_by("name")
    if request.method == "POST":
        description = request.POST.get("description", "").strip()
        priority = request.POST.get("priority", "MEDIUM").strip().upper()
        asset_id = request.POST.get("asset", "").strip()
        valid_priorities = {choice[0] for choice in MaintenanceTicket.PRIORITY_CHOICES}
        asset = None
        if asset_id:
            asset = get_object_or_404(assets, pk=asset_id)
        if not description:
            messages.error(request, "Please describe the maintenance issue.")
        elif priority not in valid_priorities:
            messages.error(request, "Please select a valid priority.")
        else:
            ticket = MaintenanceTicket.objects.create(location=location, asset=asset, description=description, priority=priority, reported_by=request.user)
            messages.success(request, f"Issue at {location.name} reported successfully (ticket #{ticket.pk}).")
            if role == "MAINTENANCE":
                return redirect("maintenance_dashboard")
            return redirect("maintenance_create_ticket_room")
    return render(request, "maintenance/create_ticket_location.html", {"location": location, "assets": assets})


@role_required("MAINTENANCE", "ADMIN")
def maintenance_locations(request):
    hotels = get_accessible_hotels(request.user)
    if request.user.role in {"MAINTENANCE", "MANAGER"}:
        if not request.user.hotel_id:
            raise PermissionDenied
        hotels = hotels.filter(pk=request.user.hotel_id)

    departments = Department.objects.filter(
        hotel__in=hotels, is_active=True
    ).select_related("hotel").order_by("hotel__name", "name")
    locations = MaintenanceLocation.objects.filter(
        hotel__in=hotels
    ).select_related("hotel").prefetch_related("responsible_departments").order_by(
        "hotel__name", "name"
    )

    search_query = request.GET.get("q", "").strip()
    if search_query:
        locations = locations.filter(
            Q(name__icontains=search_query)
            | Q(building__icontains=search_query)
            | Q(floor__icontains=search_query)
            | Q(area_code__icontains=search_query)
            | Q(description__icontains=search_query)
        )

    edit_location = None
    edit_id = request.GET.get("edit")
    if edit_id:
        edit_location = get_object_or_404(locations, pk=edit_id)

    if request.method == "POST":
        hotel = get_object_or_404(hotels, pk=request.POST.get("hotel"))
        name = request.POST.get("name", "").strip()
        location_type = request.POST.get("location_type", "OTHER").strip().upper()
        building = request.POST.get("building", "").strip()
        floor = request.POST.get("floor", "").strip()
        area_code = request.POST.get("area_code", "").strip()
        description = request.POST.get("description", "").strip()
        valid_types = {choice[0] for choice in MaintenanceLocation.LOCATION_TYPES}
        duplicate = MaintenanceLocation.objects.filter(
            hotel=hotel, name__iexact=name
        ).exists()
        if not name:
            messages.error(request, "Location name is required.")
        elif location_type not in valid_types:
            messages.error(request, "Select a valid location type.")
        elif duplicate:
            messages.error(request, "A location with that name already exists for this hotel.")
        else:
            location = MaintenanceLocation.objects.create(
                hotel=hotel, name=name, location_type=location_type,
                building=building, floor=floor, area_code=area_code,
                description=description,
            )
            location.responsible_departments.set(
                departments.filter(hotel=hotel, id__in=request.POST.getlist("departments"))
            )
            messages.success(request, f"Location '{location.name}' created.")
            return redirect(f"{reverse('maintenance_locations')}?location={location.pk}")

    selected_location = None
    selected_id = request.GET.get("location")
    if edit_location:
        selected_location = edit_location
    elif selected_id:
        selected_location = get_object_or_404(locations, pk=selected_id)
    else:
        selected_location = locations.first()

    location_assets = MaintenanceAsset.objects.none()
    location_tickets = MaintenanceTicket.objects.none()
    location_schedules = MaintenanceSchedule.objects.none()
    open_ticket_count = 0
    if selected_location:
        location_assets = MaintenanceAsset.objects.filter(
            location=selected_location
        ).select_related("product").order_by("name")
        location_tickets = MaintenanceTicket.objects.filter(
            location=selected_location
        ).select_related("room", "reported_by", "assigned_to", "asset").order_by("-created_at")
        open_ticket_count = location_tickets.exclude(status="RESOLVED").count()
        location_schedules = MaintenanceSchedule.objects.filter(
            asset__location=selected_location, is_active=True, asset__is_active=True
        ).select_related("asset").order_by("next_due_date", "name")

    return render(request, "maintenance/locations.html", {
        "hotels": hotels,
        "departments": departments,
        "locations": locations,
        "location_types": MaintenanceLocation.LOCATION_TYPES,
        "selected_location": selected_location,
        "edit_location": edit_location,
        "location_assets": location_assets,
        "location_tickets": location_tickets[:12],
        "location_schedules": location_schedules[:8],
        "location_equipment_count": location_assets.count(),
        "location_open_ticket_count": open_ticket_count,
        "search_query": search_query,
    })


@role_required("MAINTENANCE", "ADMIN")
def maintenance_location_edit(request, location_id):
    hotels = get_accessible_hotels(request.user)
    if request.user.role in {"MAINTENANCE", "MANAGER"}:
        if not request.user.hotel_id:
            raise PermissionDenied
        hotels = hotels.filter(pk=request.user.hotel_id)
    location = get_object_or_404(MaintenanceLocation, pk=location_id, hotel__in=hotels)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        location_type = request.POST.get("location_type", "OTHER").strip().upper()
        valid_types = {choice[0] for choice in MaintenanceLocation.LOCATION_TYPES}
        duplicate = MaintenanceLocation.objects.filter(
            hotel=location.hotel, name__iexact=name
        ).exclude(pk=location.pk).exists()
        if not name:
            messages.error(request, "Location name is required.")
        elif location_type not in valid_types:
            messages.error(request, "Select a valid location type.")
        elif duplicate:
            messages.error(request, "A location with that name already exists for this hotel.")
        else:
            location.name = name
            location.location_type = location_type
            location.building = request.POST.get("building", "").strip()
            location.floor = request.POST.get("floor", "").strip()
            location.area_code = request.POST.get("area_code", "").strip()
            location.description = request.POST.get("description", "").strip()
            location.save(update_fields=[
                "name", "location_type", "building", "floor", "area_code", "description"
            ])
            departments = Department.objects.filter(
                hotel=location.hotel, is_active=True,
                id__in=request.POST.getlist("departments")
            )
            location.responsible_departments.set(departments)
            messages.success(request, f"Location '{location.name}' updated.")
            return redirect(f"{reverse('maintenance_locations')}?location={location.pk}")
    return redirect(f"{reverse('maintenance_locations')}?location={location.pk}&edit={location.pk}")


@require_POST
@role_required("MAINTENANCE", "ADMIN")
def maintenance_location_toggle_status(request, location_id):
    hotels = get_accessible_hotels(request.user)
    if request.user.role in {"MAINTENANCE", "MANAGER"}:
        if not request.user.hotel_id:
            raise PermissionDenied
        hotels = hotels.filter(pk=request.user.hotel_id)
    location = get_object_or_404(MaintenanceLocation, pk=location_id, hotel__in=hotels)
    location.is_active = not location.is_active
    location.status = "ACTIVE" if location.is_active else "INACTIVE"
    location.save(update_fields=["is_active", "status"])
    messages.success(request, "Location status updated.")
    return redirect("maintenance_locations")


@role_required(
    "MAINTENANCE",
    "ADMIN",
)
def maintenance_dashboard(request):

    # --------------------------------------------------------
    # Determine hotel scope for every role.
    # Managers and Maintenance staff see their assigned hotel;
    # Directors see their organization; Admins see all hotels.
    # --------------------------------------------------------

    accessible_hotels = get_accessible_hotels(request.user)
    if not accessible_hotels.exists():
        raise PermissionDenied

    # --------------------------------------------------------
    # Base ticket queryset
    # --------------------------------------------------------

    tickets = MaintenanceTicket.objects.filter(
        Q(room__hotel__in=accessible_hotels)
        | Q(location__hotel__in=accessible_hotels)
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
            "location",
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
    "ADMIN",
)
def maintenance_ticket_detail(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket.objects
        .select_related(
            "room",
            "location",
            "asset",
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

        ticket_hotel_id = ticket.room.hotel_id if ticket.room_id else ticket.location.hotel_id
        if ticket_hotel_id != request.user.hotel_id:
            raise PermissionDenied

    # --------------------------------------------------------
    # Add work note
    # --------------------------------------------------------

    if request.method == "POST":

        # Only Maintenance staff can add work notes.
        if request.user.role != "MAINTENANCE":
            raise PermissionDenied

        # Technician must be assigned to this ticket.
        if request.user.hotel_id:
            ticket_hotel_id = ticket.room.hotel_id if ticket.room_id else ticket.location.hotel_id
            if ticket_hotel_id != request.user.hotel_id:
                raise PermissionDenied
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
@require_POST
def start_ticket(request, ticket_id):
    """
    Claim an open ticket through an explicit POST action.

    Locking the row prevents two technicians from claiming the same ticket
    concurrently. Hotel access is checked against the shared access service,
    rather than assuming every maintenance user has one hotel_id.
    """
    hotels = get_accessible_hotels(request.user)

    with transaction.atomic():
        ticket = get_object_or_404(
            MaintenanceTicket.objects
            .select_for_update()
            .select_related("room", "location"),
            pk=ticket_id,
        )

        ticket_hotel_id = (
            ticket.room.hotel_id if ticket.room_id
            else ticket.location.hotel_id if ticket.location_id
            else None
        )
        if ticket_hotel_id is None or not hotels.filter(pk=ticket_hotel_id).exists():
            raise PermissionDenied

        if ticket.status != "OPEN":
            messages.warning(request, "This maintenance ticket is no longer open.")
            return redirect("maintenance_dashboard")

        ticket.assigned_to = request.user
        ticket.status = "IN_PROGRESS"
        ticket.save(update_fields=["assigned_to", "status"])

    location_label = (
        f"Room {ticket.room.room_number}" if ticket.room_id
        else ticket.location.name
    )
    messages.success(
        request,
        f"Maintenance ticket for {location_label} is now in progress.",
    )
    return redirect("maintenance_dashboard")


# ============================================================
# COMPLETE MAINTENANCE WORK ORDER
# ============================================================

@role_required("MAINTENANCE")
def resolve_ticket(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket.objects.select_related("room", "location"),
        id=ticket_id,
    )

    if not request.user.hotel_id:
        raise PermissionDenied
    ticket_hotel_id = ticket.room.hotel_id if ticket.room_id else ticket.location.hotel_id
    if ticket_hotel_id != request.user.hotel_id:
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
    "ADMIN",
)
@transaction.atomic
def add_labour(request, ticket_id):

    ticket = get_object_or_404(
        MaintenanceTicket.objects.select_related("room", "location"),
        id=ticket_id,
    )

    # Resolve the ticket's hotel from either supported target.
    ticket_hotel_id = (
        ticket.room.hotel_id if ticket.room_id
        else ticket.location.hotel_id if ticket.location_id
        else None
    )
    if ticket_hotel_id is None:
        raise PermissionDenied

    # Preserve hotel scoping for operational roles.
    if request.user.role in ("MAINTENANCE", "MANAGER"):
        if not request.user.hotel_id or ticket_hotel_id != request.user.hotel_id:
            raise PermissionDenied

    if ticket.status != "IN_PROGRESS":
        messages.warning(
            request,
            "Labour can only be added to an in-progress work order.",
        )
        return redirect("maintenance_ticket_detail", ticket_id=ticket.id)

    technicians = User.objects.filter(
        role="MAINTENANCE",
        hotel_id=ticket_hotel_id,
        is_active=True,
    ).order_by("username")

    suppliers = Supplier.objects.filter(
        hotel_id=ticket_hotel_id,
    ).order_by("name")

    if request.method == "POST":
        labour_type = request.POST.get("labour_type", "").strip()
        description = request.POST.get("description", "").strip()
        amount_value = request.POST.get("amount", "0").strip()
        technician_id = request.POST.get("technician")
        supplier_id = request.POST.get("supplier")

        if labour_type not in ("INTERNAL", "OUTSOURCED"):
            messages.error(request, "Select a valid labour type.")
            return redirect(request.path)

        if not description:
            messages.error(request, "A description of the labour is required.")
            return redirect(request.path)

        try:
            amount = Decimal(amount_value)
            if amount < 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Please enter a valid non-negative labour amount.")
            return redirect(request.path)

        technician = None
        supplier = None

        if labour_type == "INTERNAL":
            if not technician_id:
                messages.error(request, "Please select the technician.")
                return redirect(request.path)
            technician = get_object_or_404(
                technicians,
                pk=technician_id,
            )

        else:
            if not supplier_id:
                messages.error(request, "Please select the supplier or contractor.")
                return redirect(request.path)
            supplier = get_object_or_404(
                suppliers,
                pk=supplier_id,
            )

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

        messages.success(request, "Maintenance labour added successfully.")
        return redirect("maintenance_ticket_detail", ticket_id=ticket.id)

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
        MaintenanceTicket.objects.select_related("room", "location"),
        id=ticket_id,
    )

    # --------------------------------------------------------
    # Hotel security
    # --------------------------------------------------------

    if not request.user.hotel_id:
        raise PermissionDenied
    ticket_hotel_id = ticket.room.hotel_id if ticket.room_id else ticket.location.hotel_id
    if ticket_hotel_id != request.user.hotel_id:
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
        hotel_id=ticket_hotel_id,
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
    "ADMIN",
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
                "location",
                "product",
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
                "location",
                "product",
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


@role_required("MAINTENANCE", "ADMIN")
def maintenance_asset_create(request, asset_id=None):

    hotels = get_accessible_hotels(request.user)
    if request.user.hotel_id:
        hotels = hotels.filter(pk=request.user.hotel_id)
    if not hotels.exists():
        raise PermissionDenied

    selected_hotel_id = request.POST.get("hotel") if request.method == "POST" else request.GET.get("hotel")
    selected_location_id = request.POST.get("location") if request.method == "POST" else request.GET.get("location")
    if selected_hotel_id:
        hotel = get_object_or_404(hotels, pk=selected_hotel_id)
    elif hotels.count() == 1:
        hotel = hotels.first()
    else:
        hotel = None

    if asset_id:
        if hotel is None:
            raise PermissionDenied
        asset = get_object_or_404(MaintenanceAsset, pk=asset_id, hotel=hotel)
    else:
        asset = None

    products = Product.objects.filter(hotel=hotel, product_type="EQUIPMENT", is_active=True).order_by("name") if hotel else Product.objects.none()
    if request.method == "POST":

        if hotel is None:
            messages.error(request, "Select a hotel before registering equipment.")
            return redirect(request.path)

        name = request.POST.get(
            "name",
            "",
        ).strip()
        product_id = request.POST.get("product", "").strip()

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
        purchase_date_raw = request.POST.get("purchase_date", "").strip()
        warranty_expiry_raw = request.POST.get("warranty_expiry", "").strip()
        condition = request.POST.get("condition", "GOOD").strip().upper()
        purchase_cost_raw = request.POST.get("purchase_cost", "").strip()

        room_id = request.POST.get("room", "").strip()
        location_id = request.POST.get("location", "").strip()

        if not name and not product_id:

            messages.error(
                request,
                "Asset name or equipment product is required.",
            )

            return redirect(request.path)

        if room_id and location_id:
            messages.error(request, "Choose either a room or a non-room location, not both.")
            return redirect(request.path)

        product = None
        if product_id:
            product = get_object_or_404(products, pk=product_id)
            if not name:
                name = product.name
            if not asset_type:
                asset_type = product.name

        try:
            purchase_cost = Decimal(purchase_cost_raw or "0")
            purchase_date = date.fromisoformat(purchase_date_raw) if purchase_date_raw else None
            warranty_expiry = date.fromisoformat(warranty_expiry_raw) if warranty_expiry_raw else None
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter valid purchase cost and dates.")
            return redirect(request.path)

        room = None
        location = None
        if room_id:
            room = get_object_or_404(Room, id=room_id, hotel=hotel)
        if location_id:
            location = get_object_or_404(MaintenanceLocation, id=location_id, hotel=hotel, is_active=True)

        if asset is None:
            asset = MaintenanceAsset(hotel=hotel)
        asset.room = room
        asset.location = location
        asset.product = product
        asset.name = name
        asset.asset_type = asset_type
        asset.serial_number = serial_number
        asset.manufacturer = manufacturer
        asset.model_number = model_number
        asset.purchase_date = purchase_date
        asset.purchase_cost = purchase_cost
        asset.warranty_expiry = warranty_expiry
        valid_conditions = {choice[0] for choice in MaintenanceAsset.CONDITION_CHOICES}
        if condition not in valid_conditions:
            messages.error(request, "Select a valid equipment condition.")
            return redirect(request.path)
        asset.condition = condition
        try:
            asset.full_clean()
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(request.path)
        asset.save()

        messages.success(request, "Equipment asset saved successfully.")
        return redirect("maintenance_assets")

    rooms = (
        Room.objects
        .filter(
            hotel=hotel,
        )
        .order_by(
            "room_number",
        )
    )

    locations = MaintenanceLocation.objects.filter(hotel=hotel, is_active=True, status="ACTIVE").order_by("name")
    return render(
        request,
        "maintenance/asset_form.html",
        {
            "rooms": rooms,
            "locations": locations,
            "products": products,
            "hotels": hotels,
            "selected_hotel": hotel,
            "asset": asset,
            "is_edit": bool(asset),
            "condition_choices": MaintenanceAsset.CONDITION_CHOICES,
            "selected_location_id": selected_location_id,
        },
    )

# ============================================================
# PREVENTIVE MAINTENANCE SCHEDULES
# ============================================================

@role_required(
    "MAINTENANCE",
    "ADMIN",
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

        schedule = MaintenanceSchedule.objects.create(
            asset=asset,
            name=name,
            interval_days=interval_days,
            next_due_date=next_due_date,
        )
        MaintenanceScheduleCycle.objects.get_or_create(
            schedule=schedule,
            due_date=schedule.next_due_date,
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
    "ADMIN",
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
            "asset__location",
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
    "ADMIN",
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
            Q(room__hotel_id=request.user.hotel_id) | Q(location__hotel_id=request.user.hotel_id),
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
    # Summary metrics (before applying the selected filters)
    # --------------------------------------------------------

    work_order_summary = {
        "total": tickets.count(),
        "open": tickets.filter(status="OPEN").count(),
        "in_progress": tickets.filter(status="IN_PROGRESS").count(),
        "unassigned": tickets.filter(assigned_to__isnull=True).count(),
    }

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

    target = request.GET.get(
        "target",
        request.GET.get("room", ""),
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
    # Room or common-area location search
    # --------------------------------------------------------

    if target:
        tickets = tickets.filter(
            Q(room__room_number__icontains=target)
            | Q(location__name__icontains=target)
        )

    # --------------------------------------------------------
    # Final queryset
    # --------------------------------------------------------

    tickets = (
        tickets
        .select_related(
            "room",
            "location",
            "asset",
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
            "target_filter": target,
            "work_order_summary": work_order_summary,
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
            "location",
            "asset",
            "reported_by",
            "assigned_to",
        )
        .filter(
            Q(room__hotel_id=request.user.hotel_id) | Q(location__hotel_id=request.user.hotel_id),
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
    "ADMIN",
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
                "location",
                "asset",
                "reported_by",
                "assigned_to",
            )
            .prefetch_related(
                "labour_entries",
                "material_entries",
            )
            .filter(
                status="RESOLVED",
            ).filter(
                Q(room__hotel_id=request.user.hotel_id) | Q(location__hotel_id=request.user.hotel_id),
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
    "ADMIN",
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

    total_stock_value = sum(
        (stock.stock_value for stock in stock_items),
        Decimal("0.00"),
    )
    hotels_with_stock = len({
        stock.department.hotel_id for stock in stock_items
    })

    return render(
        request,
        "maintenance/stock.html",
        {
            "maintenance_departments": maintenance_departments,
            "stock_items": stock_items,
            "accessible_hotels": accessible_hotels,
            "total_stock_value": total_stock_value,
            "hotels_with_stock": hotels_with_stock,
        },
    )

@role_required(
    "MAINTENANCE",
    "ADMIN",
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
    "ADMIN",
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
    "ADMIN",
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
    "ADMIN",
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
    "ADMIN",
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

# ============================================================
# WORK-ORDER DIRECT PURCHASES
# ============================================================

@role_required("MAINTENANCE")
@transaction.atomic
def maintenance_direct_purchase_request(request, ticket_id):
    ticket = get_object_or_404(
        MaintenanceTicket.objects.select_related("room__hotel", "location__hotel", "assigned_to"),
        pk=ticket_id,
        status="IN_PROGRESS",
    )
    hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel
    if ticket.assigned_to_id != request.user.id or request.user.hotel_id != hotel.id:
        raise PermissionDenied("Only the assigned technician may request parts for this work order.")
    department = get_object_or_404(
        Department, hotel=hotel, code="MNT", department_type="MAINTENANCE", is_active=True
    )
    products = Product.objects.filter(
        hotel=hotel, is_active=True, usage_type="INTERNAL",
        departments=department,
    ).exclude(product_type="EQUIPMENT").order_by("name")

    if request.method == "POST":
        selected_product_id = (request.POST.get("product") or "").strip()
        item_name = (request.POST.get("item_name") or "").strip()
        item_unit = (request.POST.get("item_unit") or "pcs").strip()[:20] or "pcs"
        product = None
        if selected_product_id and selected_product_id != "NEW":
            product = get_object_or_404(products, pk=selected_product_id)
            item_name = product.name
            item_unit = product.purchase_unit or product.base_unit or "pcs"
        elif selected_product_id == "NEW":
            if not item_name:
                messages.error(request, "Enter the name of the item you need.")
            elif Product.objects.filter(hotel=hotel, name__iexact=item_name).exists():
                existing = Product.objects.filter(hotel=hotel, name__iexact=item_name).first()
                messages.error(request, f"{existing.name} already exists in the catalogue. Select it from the list.")
        else:
            messages.error(request, "Select a catalogue item or choose 'Not in catalogue'.")

        try:
            quantity = int(request.POST.get("quantity", "0"))
        except (TypeError, ValueError):
            quantity = 0
        reason = request.POST.get("reason", "").strip()
        if quantity <= 0:
            messages.error(request, "Enter a whole-number quantity greater than zero.")
        elif (product or (selected_product_id == "NEW" and item_name and not Product.objects.filter(hotel=hotel, name__iexact=item_name).exists())):
            duplicate_filter = {"ticket": ticket, "status": "PENDING"}
            if product:
                duplicate_filter["product"] = product
            else:
                duplicate_filter["requested_item_name__iexact"] = item_name
                duplicate_filter["product__isnull"] = True
            if MaintenanceDirectPurchaseRequest.objects.filter(**duplicate_filter).exists():
                messages.error(request, "There is already a pending request for this item on this work order.")
            else:
                MaintenanceDirectPurchaseRequest.objects.create(
                    ticket=ticket, product=product,
                    requested_item_name="" if product else item_name,
                    requested_unit=item_unit,
                    requested_quantity=quantity,
                    reason=reason, requested_by=request.user,
                )
                messages.success(request, "Direct purchase request submitted for management approval.")
                return redirect("maintenance_ticket_detail", ticket_id=ticket.pk)

    return render(request, "maintenance/direct_purchase_request.html", {
        "ticket": ticket, "products": products,
    })


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def maintenance_direct_purchase_requests(request):
    # Keep old bookmarks working while routing everyone to the unified
    # Direct Purchases register under Approvals.
    return redirect("direct_purchase_list")


@role_required("MANAGER", "ADMIN", "DIRECTOR")
@transaction.atomic
def maintenance_direct_purchase_review(request, request_id):
    hotels = get_accessible_hotels(request.user)
    req = get_object_or_404(
        MaintenanceDirectPurchaseRequest.objects.select_for_update().select_related(
            "ticket__room__hotel", "ticket__location__hotel", "product"
        ),
        pk=request_id,
    )
    hotel = req.ticket.room.hotel if req.ticket.room_id else req.ticket.location.hotel
    if not hotels.filter(pk=hotel.pk).exists():
        raise PermissionDenied
    if request.user.role == "MANAGER" and request.user.hotel_id != hotel.id:
        raise PermissionDenied

    if request.method == "POST":
        if req.status != "PENDING":
            messages.error(request, "This request has already been reviewed.")
            return redirect("maintenance_direct_purchase_requests")
        action = request.POST.get("action")
        note = request.POST.get("manager_note", "").strip()
        if action == "reject":
            req.status = "REJECTED"
            req.manager_note = note
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.save(update_fields=["status", "manager_note", "reviewed_by", "reviewed_at"])
            messages.success(request, "Direct purchase request rejected.")
            return redirect("maintenance_direct_purchase_requests")
        if action == "approve":
            department = get_object_or_404(
                Department, hotel=hotel, code="MNT", department_type="MAINTENANCE", is_active=True
            )
            product = req.product
            if product is None:
                item_name = (req.requested_item_name or "").strip()
                if not item_name:
                    raise ValidationError("The requested item needs a name before it can be approved.")
                product = Product.objects.filter(hotel=hotel, name__iexact=item_name).first()
                if product is None:
                    # Catalogue creation is performed by the approving manager,
                    # never by the technician. A SERVICE/INTERNAL record keeps
                    # this ad-hoc part out of resale, food and fixed-asset flows.
                    product = Product.objects.create(
                        hotel=hotel,
                        name=item_name,
                        sku=f"MNT-REQ-{hotel.pk}-{req.pk}",
                        product_type="SERVICE",
                        usage_type="INTERNAL",
                        supply_source="DIRECT",
                        base_unit=req.requested_unit or "pcs",
                        purchase_unit=req.requested_unit or "pcs",
                        unit_multiplier=1,
                        cost_price=Decimal("0.00"),
                        purchase_cost=Decimal("0.00"),
                    )
                    product.departments.add(department)
                elif product.usage_type != "INTERNAL" or product.product_type == "EQUIPMENT":
                    raise ValidationError("An existing catalogue item with this name is not eligible for direct maintenance purchase.")
                else:
                    product.departments.add(department)
                req.product = product
                req.save(update_fields=["product"])
            if product.hotel_id != hotel.id or product.usage_type != "INTERNAL" or product.product_type == "EQUIPMENT":
                raise ValidationError("Only internal maintenance consumables can be purchased directly.")
            po = PurchaseOrder.objects.create(
                department=department, created_by=request.user, status="DRAFT"
            )
            PurchaseItem.objects.create(
                purchase_order=po, product=product,
                purchase_quantity=req.requested_quantity, unit_cost=Decimal("0.00"),
            )
            req.status = "APPROVED"
            req.manager_note = note
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.purchase_order = po
            req.save(update_fields=[
                "status", "manager_note", "reviewed_by", "reviewed_at", "purchase_order"
            ])
            messages.success(request, f"Approved. Draft Purchase Order #{po.pk} created; add supplier and cost before approval.")
            return redirect("inventory:po_detail", pk=po.pk)
        messages.error(request, "Choose approve or reject.")

    return render(request, "maintenance/direct_purchase_review.html", {"purchase_request": req})
