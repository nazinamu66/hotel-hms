from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from rooms.models import Room
from django.contrib import messages
from .models import CleaningAssignment,LostFoundItem
from accounts.models import User
from django.core.exceptions import PermissionDenied


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

@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def assign_room(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    # Only department head or manager/admin can assign
    if (
        not request.user.is_department_head
        and request.user.role not in ["MANAGER", "ADMIN"]
    ):
        raise PermissionDenied("Only the department head can assign rooms.")

    housekeepers = User.objects.filter(
        role="HOUSEKEEPING",
        department=request.user.department,
        is_active=True
    ).order_by("username")

    if request.method == "POST":

        user_id = request.POST.get("user")

        if not user_id:
            messages.error(request, "Please select a housekeeper.")
            return redirect(request.path)

        CleaningAssignment.objects.create(
            room=room,
            assigned_to_id=user_id,
            assigned_by=request.user
        )

        messages.success(request, f"Room {room.room_number} assigned.")

        return redirect("housekeeping_dashboard")

    return render(
        request,
        "housekeeping/assign_room.html",
        {
            "room": room,
            "housekeepers": housekeepers,
        }
    )

@role_required("HOUSEKEEPING", "MANAGER", "ADMIN", "DIRECTOR")
def lost_found_list(request):

    hotel = request.user.department.hotel

    items = (
        LostFoundItem.objects
        .select_related("room", "found_by")
        .filter(room__hotel=hotel)
        .order_by("-found_at")
    )

    return render(
        request,
        "housekeeping/lost_found_list.html",
        {"items": items}
    )

@role_required("HOUSEKEEPING", "MANAGER", "ADMIN")
def lost_found_create(request):

    rooms = Room.objects.filter(
        hotel=request.user.department.hotel
    )

    if request.method == "POST":

        room_id = request.POST.get("room")
        description = request.POST.get("description")

        LostFoundItem.objects.create(
            room_id=room_id if room_id else None,
            description=description,
            found_by=request.user
        )

        messages.success(request, "Item recorded successfully.")

        return redirect("housekeeping_lost_found")

    return render(
        request,
        "housekeeping/lost_found_create.html",
        {"rooms": rooms}
    )