from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from accounts.decorators import role_required
from rooms.models import Room
from .models import MaintenanceTicket

@role_required("HOUSEKEEPING", "FRONTDESK", "MANAGER", "DIRECTOR")
def create_ticket(request, room_id):

    room = get_object_or_404(Room, id=room_id)

    if request.method == "POST":

        description = request.POST.get("description")
        priority = request.POST.get("priority")

        MaintenanceTicket.objects.create(
            room=room,
            description=description,
            priority=priority,
            reported_by=request.user
        )

        messages.success(request, "Maintenance ticket created.")

        return redirect("housekeeping_dashboard")

    return render(
        request,
        "maintenance/create_ticket.html",
        {"room": room}
    )

@role_required("MANAGER", "ADMIN")
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