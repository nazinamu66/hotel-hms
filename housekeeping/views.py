from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from rooms.models import Room
from django.contrib import messages
from .models import CleaningAssignment,LostFoundItem
from accounts.models import User
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError



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


@role_required("HOUSEKEEPING","MANAGER","ADMIN",)
def mark_clean(request, room_id):

    from housekeeping.workflows.clean_room import (
        clean_room,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    try:

        clean_room(
            room=room,
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

    except Exception as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "housekeeping_dashboard",
        )

    messages.success(
        request,
        f"Room {room.room_number} cleaned successfully.",
    )

    return redirect(
        "housekeeping_dashboard",
    )

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

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
)
def assign_room(request, room_id):

    from housekeeping.workflows.assign_room import (
        assign_room as assign_room_workflow,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    housekeepers = User.objects.filter(
        role="HOUSEKEEPING",
        department=request.user.department,
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

        except Exception as e:

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

@role_required(
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
)
def lost_found_create(request):

    from housekeeping.workflows.lost_found import (
        record_item,
    )

    rooms = Room.objects.filter(
        hotel=request.user.department.hotel,
    )

    if request.method == "POST":

        room = None

        room_id = request.POST.get("room")

        if room_id:

            room = get_object_or_404(
                Room,
                id=room_id,
                hotel=request.user.department.hotel,
            )

        try:

            record_item(
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
            "rooms": rooms,
        },
    )