from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from rooms.models import Room
from django.contrib import messages


@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def dashboard(request):
    dirty_rooms = Room.objects.filter(
        status__in=["VACANT_DIRTY", "OCCUPIED_DIRTY"]
    ).order_by("room_number")

    return render(
        request,
        "housekeeping/dashboard.html",
        {"rooms": dirty_rooms}
    )


@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def mark_clean(request, room_id):
    room = get_object_or_404(Room, id=room_id)

    if room.status not in ["VACANT_DIRTY", "OCCUPIED_DIRTY"]:
        messages.error(request, "Room does not need cleaning.")
        return redirect("/housekeeping/")

    room.status = "AVAILABLE"
    room.save()

    messages.success(
        request,
        f"Room {room.room_number} marked as clean and available."
    )
    return redirect("/housekeeping/")
