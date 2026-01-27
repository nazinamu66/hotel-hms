from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test

from accounts.decorators import manager_or_admin
from .models import Building, RoomCategory, Room


@user_passes_test(manager_or_admin)
def building_list(request):
    buildings = Building.objects.all()
    return render(
        request,
        "rooms/building_list.html",
        {"buildings": buildings}
    )


@user_passes_test(manager_or_admin)
def building_create(request):
    if request.method == "POST":
        name = request.POST.get("name")

        if not name:
            messages.error(request, "Building name is required.")
            return redirect("building_create")

        Building.objects.create(name=name)
        messages.success(request, f"Building '{name}' created.")
        return redirect("building_list")

    return render(request, "rooms/building_form.html")


@user_passes_test(manager_or_admin)
def building_toggle_active(request, pk):
    building = get_object_or_404(Building, pk=pk)
    building.is_active = not building.is_active
    building.save(update_fields=["is_active"])

    state = "activated" if building.is_active else "deactivated"
    messages.success(request, f"Building {building.name} {state}.")
    return redirect("building_list")

from django.contrib.auth.decorators import user_passes_test

def manager_or_admin(user):
    return user.role in ["MANAGER", "ADMIN"]

@user_passes_test(manager_or_admin)
def category_list(request):
    categories = RoomCategory.objects.all()

    return render(
        request,
        "rooms/category_list.html",
        {"categories": categories}
    )

@user_passes_test(manager_or_admin)
def category_create(request):
    if request.method == "POST":
        name = request.POST.get("name")
        description = request.POST.get("description", "")

        if not name:
            messages.error(request, "Category name is required.")
            return redirect("category_create")

        RoomCategory.objects.create(
            name=name,
            description=description
        )

        messages.success(request, "Room category created.")
        return redirect("category_list")

    return render(request, "rooms/category_form.html")
@user_passes_test(manager_or_admin)
def category_edit(request, pk):
    category = get_object_or_404(RoomCategory, pk=pk)

    if request.method == "POST":
        category.name = request.POST.get("name")
        category.description = request.POST.get("description", "")
        category.save()

        messages.success(request, "Category updated.")
        return redirect("category_list")

    return render(
        request,
        "rooms/category_form.html",
        {"category": category}
    )

from django.contrib.auth.decorators import user_passes_test

def is_manager_or_admin(user):
    return user.role in ["ADMIN", "MANAGER"]


@user_passes_test(is_manager_or_admin)
def room_list(request):
    rooms = Room.objects.select_related(
        "category", "building"
    ).order_by("room_number")

    return render(
        request,
        "rooms/room_list.html",
        {"rooms": rooms}
    )

@user_passes_test(is_manager_or_admin)
def room_create(request):
    categories = RoomCategory.objects.all()
    buildings = Building.objects.all()

    if request.method == "POST":
        room_number = request.POST.get("room_number")
        category_id = request.POST.get("category")
        building_id = request.POST.get("building")
        status = request.POST.get("status")

        if not room_number or not category_id:
            messages.error(request, "Room number and category are required.")
            return redirect("room_create")

        Room.objects.create(
            room_number=room_number,
            category_id=category_id,
            building_id=building_id or None,
            status=status or "AVAILABLE"
        )

        messages.success(request, "Room created successfully.")
        return redirect("room_list")

    return render(
        request,
        "rooms/room_form.html",
        {
            "categories": categories,
            "buildings": buildings,
            "statuses": Room.STATUS_CHOICES,
        }
    )

@user_passes_test(is_manager_or_admin)
def room_edit(request, pk):
    room = get_object_or_404(Room, pk=pk)
    categories = RoomCategory.objects.all()
    buildings = Building.objects.all()

    if request.method == "POST":
        room.room_number = request.POST.get("room_number")
        room.category_id = request.POST.get("category")
        room.building_id = request.POST.get("building") or None
        room.status = request.POST.get("status")

        room.save()
        messages.success(request, "Room updated successfully.")
        return redirect("room_list")

    return render(
        request,
        "rooms/room_form.html",
        {
            "room": room,
            "categories": categories,
            "buildings": buildings,
            "statuses": Room.STATUS_CHOICES,
        }
    )
