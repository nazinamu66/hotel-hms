from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from accounts.decorators import manager_admin_or_director
from .models import (
    Building,
    RoomCategory,
    Room,
    Floor,
    RoomRate,
    Amenity,
)
from .workflows.assign_amenity import (
    assign_amenity,
)

from .workflows.remove_amenity import (
    remove_amenity,
)


@user_passes_test(manager_admin_or_director)
def building_list(request):
    buildings = Building.objects.all()
    return render(
        request,
        "rooms/building_list.html",
        {"buildings": buildings}
    )


@user_passes_test(manager_admin_or_director)
def building_create(request):

    if not request.user.hotel:
        messages.error(request, "Your account is not assigned to a hotel.")
        return redirect("owner_dashboard")

    if request.method == "POST":

        name = request.POST.get("name")

        if not name:
            messages.error(request, "Building name is required.")
            return redirect("building_create")

        Building.objects.create(
            hotel=request.user.hotel,
            name=name
        )

        messages.success(request, f"Building '{name}' created.")
        return redirect("building_list")

    return render(request, "rooms/building_form.html")


@user_passes_test(manager_admin_or_director)
def building_toggle_active(request, pk):
    building = get_object_or_404(Building, pk=pk)
    building.is_active = not building.is_active
    building.save(update_fields=["is_active"])

    state = "activated" if building.is_active else "deactivated"
    messages.success(request, f"Building {building.name} {state}.")
    return redirect("building_list")

from django.contrib.auth.decorators import user_passes_test

def manager_admin_or_director(user):
    return user.is_authenticated and user.role in ["MANAGER", "ADMIN", "DIRECTOR"]

@user_passes_test(manager_admin_or_director)
def category_list(request):
    categories = RoomCategory.objects.all()

    return render(
        request,
        "rooms/category_list.html",
        {"categories": categories}
    )

@user_passes_test(manager_admin_or_director)
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

@user_passes_test(manager_admin_or_director)
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

def manager_admin_or_director(user):
    return user.is_authenticated and user.role in ["MANAGER", "ADMIN", "DIRECTOR"]


@user_passes_test(manager_admin_or_director)
def room_list(request):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )

        return redirect(
            "owner_dashboard",
        )

    rooms = (
        Room.objects
        .filter(
            hotel=request.user.hotel,
        )
        .select_related(
            "category",
            "building",
            "floor",
        )
        .prefetch_related(
            "room_amenities__amenity",
        )
        .order_by(
            "room_number",
        )
    )

    return render(
        request,
        "rooms/room_list.html",
        {
            "rooms": rooms,
        },
    )
@user_passes_test(manager_admin_or_director)
def room_create(request):

    if not request.user.hotel:
        messages.error(request, "Your account is not assigned to a hotel.")
        return redirect("owner_dashboard")

    categories = RoomCategory.objects.all()

    buildings = Building.objects.filter(
        hotel=request.user.hotel
    )

    floors = Floor.objects.filter(
        building__hotel=request.user.hotel
    )

    if request.method == "POST":

        room_number = request.POST.get("room_number")
        category_id = request.POST.get("category")
        building_id = request.POST.get("building")
        floor_id = request.POST.get("floor")
        status = request.POST.get("status")

        if not room_number or not category_id:
            messages.error(request, "Room number and category are required.")
            return redirect("room_create")

        Room.objects.create(
            hotel=request.user.hotel,
            room_number=room_number,
            category_id=category_id,
            building_id=building_id or None,
            floor_id=floor_id or None,
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
            "floors": floors,
            "statuses": Room.STATUS_CHOICES,
        }
    )

@user_passes_test(manager_admin_or_director)
def room_edit(request, pk):

    if not request.user.hotel:
        messages.error(request, "Your account is not assigned to a hotel.")
        return redirect("owner_dashboard")

    room = get_object_or_404(Room,pk=pk,hotel=request.user.hotel,)

    categories = RoomCategory.objects.all()

    buildings = Building.objects.filter(
        hotel=request.user.hotel
    )

    floors = Floor.objects.filter(
        building__hotel=request.user.hotel
    )

    if request.method == "POST":

        room.room_number = request.POST.get("room_number")
        room.category_id = request.POST.get("category")
        room.building_id = request.POST.get("building") or None
        room.floor_id = request.POST.get("floor") or None
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
            "floors": floors,
            "statuses": Room.STATUS_CHOICES,
        }
    )

@user_passes_test(manager_admin_or_director)
def room_detail(request, pk):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )

        return redirect(
            "owner_dashboard",
        )

    room = get_object_or_404(
        Room.objects
        .select_related(
            "category",
            "building",
            "floor",
        )
        .prefetch_related(
            "room_amenities__amenity",
        ),
        pk=pk,
        hotel=request.user.hotel,
    )

    return render(
        request,
        "rooms/room_detail.html",
        {
            "room": room,
        },
    )

@user_passes_test(manager_admin_or_director)
def amenity_list(request):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )
        return redirect("owner_dashboard")

    amenities = (
        Amenity.objects
        .filter(
            hotel=request.user.hotel,
        )
        .order_by("name")
    )

    return render(
        request,
        "rooms/amenity_list.html",
        {
            "amenities": amenities,
        },
    )


@user_passes_test(manager_admin_or_director)
def amenity_create(request):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )
        return redirect("owner_dashboard")

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        if not name:
            messages.error(
                request,
                "Amenity name is required.",
            )

            return redirect(
                "amenity_create",
            )

        if Amenity.objects.filter(
            hotel=request.user.hotel,
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                "An amenity with this name already exists for your hotel.",
            )

            return redirect(
                "amenity_create",
            )

        Amenity.objects.create(
            hotel=request.user.hotel,
            name=name,
            description=description,
        )

        messages.success(
            request,
            f"Amenity '{name}' created.",
        )

        return redirect(
            "amenity_list",
        )

    return render(
        request,
        "rooms/amenity_form.html",
    )


@user_passes_test(manager_admin_or_director)
def amenity_edit(request, pk):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )
        return redirect("owner_dashboard")

    amenity = get_object_or_404(
        Amenity,
        pk=pk,
        hotel=request.user.hotel,
    )

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        if not name:
            messages.error(
                request,
                "Amenity name is required.",
            )

            return redirect(
                "amenity_edit",
                pk=amenity.pk,
            )

        duplicate = (
            Amenity.objects
            .filter(
                hotel=request.user.hotel,
                name__iexact=name,
            )
            .exclude(
                pk=amenity.pk,
            )
            .exists()
        )

        if duplicate:

            messages.error(
                request,
                "An amenity with this name already exists for your hotel.",
            )

            return redirect(
                "amenity_edit",
                pk=amenity.pk,
            )

        amenity.name = name
        amenity.description = description

        amenity.save(
            update_fields=[
                "name",
                "description",
            ],
        )

        messages.success(
            request,
            "Amenity updated.",
        )

        return redirect(
            "amenity_list",
        )

    return render(
        request,
        "rooms/amenity_form.html",
        {
            "amenity": amenity,
        },
    )


@user_passes_test(manager_admin_or_director)
def amenity_toggle_active(request, pk):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )
        return redirect("owner_dashboard")

    amenity = get_object_or_404(
        Amenity,
        pk=pk,
        hotel=request.user.hotel,
    )

    amenity.is_active = not amenity.is_active

    amenity.save(
        update_fields=[
            "is_active",
        ],
    )

    state = (
        "activated"
        if amenity.is_active
        else "deactivated"
    )

    messages.success(
        request,
        f"Amenity '{amenity.name}' {state}.",
    )

    return redirect(
        "amenity_list",
    )

@user_passes_test(manager_admin_or_director)
def room_amenities(request, pk):

    if not request.user.hotel:
        messages.error(
            request,
            "Your account is not assigned to a hotel.",
        )

        return redirect(
            "owner_dashboard",
        )

    room = get_object_or_404(
        Room.objects
        .select_related(
            "category",
            "building",
            "floor",
        )
        .prefetch_related(
            "room_amenities__amenity",
        ),
        pk=pk,
        hotel=request.user.hotel,
    )

    amenities = (
        Amenity.objects
        .filter(
            hotel=request.user.hotel,
            is_active=True,
        )
        .order_by(
            "name",
        )
    )

    assigned_amenity_ids = set(
        room.room_amenities.values_list(
            "amenity_id",
            flat=True,
        )
    )

    available_amenities = amenities.exclude(
        id__in=assigned_amenity_ids,
    )

    if request.method == "POST":

        action = request.POST.get(
            "action",
        )

        if action == "add":

            amenity_id = request.POST.get(
                "amenity",
            )

            quantity = request.POST.get(
                "quantity",
                "1",
            )

            try:

                quantity = int(quantity)

                assign_amenity(
                    room=room,
                    amenity_id=amenity_id,
                    quantity=quantity,
                )

                messages.success(
                    request,
                    "Amenity added to room.",
                )

            except (
                ValidationError,
                ValueError,
            ) as e:

                messages.error(
                    request,
                    str(e),
                )

        elif action == "remove":

            room_amenity_id = request.POST.get(
                "room_amenity_id",
            )

            try:

                remove_amenity(
                    room=room,
                    room_amenity_id=room_amenity_id,
                )

                messages.success(
                    request,
                    "Amenity removed from room.",
                )

            except ValidationError as e:

                messages.error(
                    request,
                    str(e),
                )

        return redirect(
            request.path,
        )

    return render(
        request,
        "rooms/room_amenities.html",
        {
            "room": room,
            "available_amenities": available_amenities,
        },
    )

@user_passes_test(manager_admin_or_director)
def floor_list(request):

    floors = Floor.objects.select_related(
        "building"
    ).order_by("building", "number")

    return render(
        request,
        "rooms/floor_list.html",
        {"floors": floors}
    )

@user_passes_test(manager_admin_or_director)
def floor_create(request):

    buildings = Building.objects.filter(
        hotel=request.user.hotel,
        is_active=True
    )

    if request.method == "POST":

        building_id = request.POST.get("building")
        name = request.POST.get("name")
        number = request.POST.get("number")

        Floor.objects.create(
            building_id=building_id,
            name=name,
            number=number
        )

        messages.success(request, "Floor created.")
        return redirect("floor_list")

    return render(
        request,
        "rooms/floor_form.html",
        {"buildings": buildings}
    )

def rate_list(request):

    rates = RoomRate.objects.select_related(
        "category"
    )

    return render(
        request,
        "rooms/rate_list.html",
        {"rates": rates}
    )

def rate_create(request):

    categories = RoomCategory.objects.all()

    if request.method == "POST":

        RoomRate.objects.update_or_create(
            category_id=request.POST.get("category"),
            defaults={
                "price_per_night": request.POST.get("price"),
                "currency": request.POST.get("currency")
            }
        )

        return redirect("rate_list")

    return render(
        request,
        "rooms/rate_form.html",
        {"categories": categories}
    )