from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from accounts.decorators import role_required
from rooms.models import Room
from .models import MaintenanceTicket

@role_required("HOUSEKEEPING", "FRONTDESK", "MANAGER", "DIRECTOR")
def create_ticket(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    if request.method == "POST":

        description = request.POST.get("description")
        priority = request.POST.get("priority")

        ticket = MaintenanceTicket.objects.create(
            room=room,
            description=description,
            priority=priority,
            reported_by=request.user
        )

        # Lock room if issue is serious
        if priority == "HIGH":
            room.status = "OUT_OF_ORDER"
            room.save(update_fields=["status"])

        messages.success(request, "Maintenance ticket created.")

        return redirect("housekeeping_dashboard")

    return render(
        request,
        "maintenance/create_ticket.html",
        {"room": room}
    )

from django.utils import timezone


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def resolve_ticket(request, ticket_id):

    ticket = get_object_or_404(MaintenanceTicket, id=ticket_id)

    ticket.status = "RESOLVED"
    ticket.resolved_at = timezone.now()
    ticket.save(update_fields=["status", "resolved_at"])

    room = ticket.room

    # Restore room if it was locked
    if room.status == "OUT_OF_ORDER":
        room.status = "AVAILABLE"
        room.save(update_fields=["status"])

    messages.success(request, "Ticket resolved.")

    return redirect("maintenance_dashboard")

@role_required("MANAGER", "ADMIN", "DIRECTOR")
def maintenance_dashboard(request):

    tickets = (
        MaintenanceTicket.objects
        .select_related("room", "reported_by")
        .exclude(status="RESOLVED")
        .order_by("-created_at")
    )

    return render(
        request,
        "maintenance/dashboard.html",
        {"tickets": tickets}
    )