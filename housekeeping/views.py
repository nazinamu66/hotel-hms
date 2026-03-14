from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from rooms.models import Room
from django.contrib import messages


@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def dashboard(request):

    hotel = request.user.department.hotel

    dirty_rooms = Room.objects.filter(
        hotel=hotel,
        status__in=["VACANT_DIRTY", "OCCUPIED_DIRTY"]
    ).order_by("room_number")

    return render(
        request,
        "housekeeping/dashboard.html",
        {"rooms": dirty_rooms}
    )


from housekeeping.models import CleaningLog


@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def mark_clean(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    if room.status not in ["VACANT_DIRTY", "OCCUPIED_DIRTY"]:
        messages.error(request, "Room does not need cleaning.")
        return redirect("/housekeeping/")

    previous_status = room.status

    # Correct operational logic
    if room.status == "VACANT_DIRTY":
        room.status = "AVAILABLE"

    elif room.status == "OCCUPIED_DIRTY":
        room.status = "OCCUPIED"

    room.save(update_fields=["status"])

    # Create cleaning log
    CleaningLog.objects.create(
        room=room,
        cleaned_by=request.user,
        previous_status=previous_status
    )

    messages.success(
        request,
        f"Room {room.room_number} cleaned successfully."
    )

    return redirect("/housekeeping/")

@role_required("HOUSEKEEPING", "MANAGER", "ADMIN", "DIRECTOR")
def cleaning_history(request):

    logs = (
        CleaningLog.objects
        .select_related("room", "cleaned_by")
        .order_by("-cleaned_at")[:100]
    )

    return render(
        request,
        "housekeeping/history.html",
        {"logs": logs}
    )