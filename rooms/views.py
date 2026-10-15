from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError,PermissionDenied
from accounts.decorators import manager_admin_or_director
from django.db import transaction
from django.db.models import Q
from decimal import Decimal, InvalidOperation
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

from accounts.services.access import (
    get_accessible_hotels,
)

def accessible_buildings(user):
    return Building.objects.filter(
        hotel__in=get_accessible_hotels(user),
        is_active=True,
    )


def accessible_floors(user):
    return Floor.objects.filter(
        building__hotel__in=get_accessible_hotels(user),
        is_active=True,
    )



@user_passes_test(manager_admin_or_director)
def building_list(request):

    buildings = (
        Building.objects
        .filter(
            hotel__in=get_accessible_hotels(
                request.user,
            ),
        )
        .select_related(
            "hotel",
        )
        .order_by(
            "hotel__name",
            "name",
        )
    )

    return render(
        request,
        "rooms/building_list.html",
        {
            "buildings": buildings,
        },
    )

@user_passes_test(manager_admin_or_director)
@transaction.atomic
def building_create(request):

    hotels = get_accessible_hotels(request.user)

    if not hotels.exists():
        messages.error(
            request,
            "You do not have access to any hotel.",
        )
        return redirect("owner_dashboard")

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        hotel_id = request.POST.get(
            "hotel",
        )

        floor_count = request.POST.get(
            "floor_count",
            "",
        ).strip()

        # -----------------------------------------------------
        # VALIDATE BUILDING
        # -----------------------------------------------------

        if not name:
            messages.error(
                request,
                "Building name is required.",
            )
            return redirect("building_create")

        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

        # -----------------------------------------------------
        # VALIDATE FLOOR COUNT
        # -----------------------------------------------------

        try:
            floor_count = int(floor_count)
        except (TypeError, ValueError):
            messages.error(
                request,
                "Number of floors must be a valid number.",
            )
            return redirect("building_create")

        if floor_count < 1:
            messages.error(
                request,
                "A building must have at least one floor.",
            )
            return redirect("building_create")

        # -----------------------------------------------------
        # DUPLICATE BUILDING CHECK
        # -----------------------------------------------------

        if Building.objects.filter(
            hotel=hotel,
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                f"A building named '{name}' already exists "
                f"in {hotel.name}.",
            )
            return redirect("building_create")

        # -----------------------------------------------------
        # CREATE BUILDING + FLOORS
        # -----------------------------------------------------

        building = Building.objects.create(
            hotel=hotel,
            name=name,
        )

        for number in range(1, floor_count + 1):

            Floor.objects.create(
                building=building,
                name=f"Floor {number}",
                number=number,
            )

        messages.success(
            request,
            f"Building '{name}' created with "
            f"{floor_count} floor(s).",
        )

        return redirect(
            "building_detail",
            pk=building.pk,
        )

    return render(
        request,
        "rooms/building_form.html",
        {
            "hotels": hotels,
        },
    )

@user_passes_test(manager_admin_or_director)
def building_detail(request, pk):

    building = get_object_or_404(
        Building.objects
        .select_related("hotel")
        .prefetch_related("floors"),
        pk=pk,
        hotel__in=get_accessible_hotels(request.user),
    )

    floors = building.floors.order_by("number")

    return render(
        request,
        "rooms/building_detail.html",
        {
            "building": building,
            "floors": floors,
        },
    )

@user_passes_test(manager_admin_or_director)
def building_toggle_active(request, pk):

    building = get_object_or_404(
        Building,
        pk=pk,
        hotel__in=get_accessible_hotels(
            request.user,
        ),
    )

    building.is_active = not building.is_active

    building.save(
        update_fields=[
            "is_active",
        ],
    )

    state = (
        "activated"
        if building.is_active
        else "deactivated"
    )

    messages.success(
        request,
        f"Building {building.name} {state}.",
    )

    return redirect(
        "building_list",
    )

from django.contrib.auth.decorators import user_passes_test

def manager_admin_or_director(user):
    return user.is_authenticated and user.role in ["MANAGER", "ADMIN", "DIRECTOR"]

@user_passes_test(manager_admin_or_director)
def category_list(request):

    categories = (
        RoomCategory.objects
        .filter(
            is_active=True,
        )
        .select_related(
            "rate",
        )
        .order_by(
            "name",
        )
    )

    return render(
        request,
        "rooms/category_list.html",
        {
            "categories": categories,
        },
    )

@user_passes_test(manager_admin_or_director)
def category_create(request):

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        price = request.POST.get(
            "price",
            "",
        ).strip()

        currency = request.POST.get(
            "currency",
            "NGN",
        ).strip().upper()

        if not name:
            messages.error(
                request,
                "Category name is required.",
            )
            return redirect(
                "category_create",
            )

        if not price:
            messages.error(
                request,
                "Room rate is required.",
            )
            return redirect(
                "category_create",
            )

        try:
            price = Decimal(price)

            if price <= 0:
                raise ValueError

        except (ValueError, TypeError, InvalidOperation):
            messages.error(
                request,
                "Enter a valid room rate greater than zero.",
            )
            return redirect(
                "category_create",
            )

        if RoomCategory.objects.filter(
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                "A room category with this name already exists.",
            )
            return redirect(
                "category_create",
            )

        with transaction.atomic():

            category = RoomCategory.objects.create(
                name=name,
                description=description,
            )

            RoomRate.objects.create(
                category=category,
                price_per_night=price,
                currency=currency,
            )

        messages.success(
            request,
            "Room category and initial rate created.",
        )

        return redirect(
            "category_list",
        )

    return render(
        request,
        "rooms/category_form.html",
    )

@user_passes_test(manager_admin_or_director)
def category_edit(request, pk):

    category = get_object_or_404(
        RoomCategory,
        pk=pk,
    )

    rate = getattr(
        category,
        "rate",
        None,
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

        price = request.POST.get(
            "price",
            "",
        ).strip()

        currency = request.POST.get(
            "currency",
            "NGN",
        ).strip().upper()

        if not name:
            messages.error(
                request,
                "Category name is required.",
            )
            return redirect(
                "category_edit",
                pk=category.pk,
            )

        if not price:
            messages.error(
                request,
                "Room rate is required.",
            )
            return redirect(
                "category_edit",
                pk=category.pk,
            )

        try:
            price = Decimal(price)

            if price <= 0:
                raise ValueError

        except (ValueError, TypeError, InvalidOperation):

            messages.error(
                request,
                "Enter a valid room rate greater than zero.",
            )

            return redirect(
                "category_edit",
                pk=category.pk,
            )

        duplicate = (
            RoomCategory.objects
            .filter(
                name__iexact=name,
            )
            .exclude(
                pk=category.pk,
            )
            .exists()
        )

        if duplicate:

            messages.error(
                request,
                "A room category with this name already exists.",
            )

            return redirect(
                "category_edit",
                pk=category.pk,
            )

        with transaction.atomic():

            category.name = name
            category.description = description
            category.save(
                update_fields=[
                    "name",
                    "description",
                ],
            )

            if rate:

                rate.price_per_night = price
                rate.currency = currency

                rate.save(
                    update_fields=[
                        "price_per_night",
                        "currency",
                        "updated_at",
                    ],
                )

            else:

                RoomRate.objects.create(
                    category=category,
                    price_per_night=price,
                    currency=currency,
                )

        messages.success(
            request,
            "Room category and rate updated.",
        )

        return redirect(
            "category_list",
        )

    return render(
        request,
        "rooms/category_form.html",
        {
            "category": category,
            "rate": rate,
        },
    )

from django.contrib.auth.decorators import user_passes_test

def manager_admin_or_director(user):
    return user.is_authenticated and user.role in ["MANAGER", "ADMIN", "DIRECTOR"]


@user_passes_test(manager_admin_or_director)
def room_list(request):

    accessible_hotels = get_accessible_hotels(
        request.user
    )

    rooms = (
        Room.objects
        .filter(
            hotel__in=accessible_hotels
        )
        .select_related(
            "category",
            "building",
            "floor",
        )
        .order_by(
            "room_number"
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

    hotels = get_accessible_hotels(
        request.user,
    )

    categories = (
        RoomCategory.objects
        .filter(is_active=True)
        .order_by("name")
    )

    buildings = (
        Building.objects
        .filter(
            hotel__in=hotels,
            is_active=True,
        )
        .select_related("hotel")
        .order_by(
            "hotel__name",
            "name",
        )
    )
    floors = (
        Floor.objects
        .filter(
            building__hotel__in=hotels,
            is_active=True,
        )
        .select_related(
            "building",
            "building__hotel",
        )
        .order_by(
            "building__hotel__name",
            "building__name",
            "number",
        )
    )
    if request.method == "POST":
        room_number = request.POST.get(
            "room_number",
            "",
        ).strip()
        category_id = request.POST.get(
            "category",
        )
        building_id = request.POST.get(
            "building",
        )
        floor_id = request.POST.get(
            "floor",
        )
        status = request.POST.get(
            "status",
        )
        hotel_id = request.POST.get(
            "hotel",
        )
        if not room_number or not category_id:
            messages.error(
                request,
                "Room number and category are required.",
            )
            return redirect("room_create")
        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )
        category = get_object_or_404(
            categories,
            pk=category_id,
        )
        building = None
        if building_id:
            building = get_object_or_404(
                buildings,
                pk=building_id,
                hotel=hotel,
            )
        floor = None
        if floor_id:
            floor = get_object_or_404(
                floors,
                pk=floor_id,
                building__hotel=hotel,
            )
        if floor and building:
            if floor.building_id != building.id:
                messages.error(
                    request,
                    "Selected floor does not belong to the selected building.",
                )
                return redirect("room_create")
        if Room.objects.filter(
            hotel=hotel,
            room_number=room_number,
        ).exists():
            messages.error(
                request,
                "A room with this number already exists in this hotel.",
            )
            return redirect("room_create")
        with transaction.atomic():
            Room.objects.create(
                hotel=hotel,
                room_number=room_number,
                category=category,
                building=building,
                floor=floor,
                status=status or "AVAILABLE",
            )
        messages.success(
            request,
            "Room created successfully.",
        )
        return redirect(
            "room_list",
        )
    return render(
        request,
        "rooms/room_form.html",
        {
            "hotels": hotels,
            "categories": categories,
            "buildings": buildings,
            "floors": floors,
            "statuses": Room.STATUS_CHOICES,
        },
    )

@user_passes_test(manager_admin_or_director)
def room_edit(request, pk):

    hotels = get_accessible_hotels(
        request.user,
    )

    room = get_object_or_404(
        Room.objects
        .select_related(
            "category",
            "building",
            "floor",
        ),
        pk=pk,
        hotel__in=hotels,
    )

    categories = (
        RoomCategory.objects
        .filter(
            is_active=True,
        )
        .order_by(
            "name",
        )
    )

    buildings = (
        Building.objects
        .filter(
            hotel__in=hotels,
            is_active=True,
        )
        .select_related(
            "hotel",
        )
        .order_by(
            "hotel__name",
            "name",
        )
    )

    floors = (
        Floor.objects
        .filter(
            building__hotel__in=hotels,
            is_active=True,
        )
        .select_related(
            "building",
            "building__hotel",
        )
        .order_by(
            "building__hotel__name",
            "building__name",
            "number",
        )
    )

    if request.method == "POST":

        room_number = request.POST.get(
            "room_number",
            "",
        ).strip()

        hotel_id = request.POST.get(
            "hotel",
        )

        category_id = request.POST.get(
            "category",
        )

        building_id = request.POST.get(
            "building",
        )

        floor_id = request.POST.get(
            "floor",
        )

        status = request.POST.get(
            "status",
            "AVAILABLE",
        )
        if not room_number:
            messages.error(
                request,
                "Room number is required.",
            )
            return redirect(
                "room_edit",
                pk=room.pk,
            )
        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )
        category = get_object_or_404(
            categories,
            pk=category_id,
        )
        building = None
        if building_id:
            building = get_object_or_404(
                buildings,
                pk=building_id,
                hotel=hotel,
            )
        floor = None
        if floor_id:
            floor = get_object_or_404(
                floors,
                pk=floor_id,
                building__hotel=hotel,
            )
        if building and floor:
            if floor.building_id != building.id:
                messages.error(
                    request,
                    "Selected floor does not belong to the selected building.",
                )

                return redirect(
                    "room_edit",
                    pk=room.pk,
                )

        duplicate = (
            Room.objects
            .filter(
                hotel=hotel,
                room_number=room_number,
            )
            .exclude(
                pk=room.pk,
            )
            .exists()
        )

        if duplicate:

            messages.error(
                request,
                "A room with this number already exists in this hotel.",
            )

            return redirect(
                "room_edit",
                pk=room.pk,
            )

        with transaction.atomic():

            room.hotel = hotel
            room.room_number = room_number
            room.category = category
            room.building = building
            room.floor = floor
            room.status = status

            room.save()

        messages.success(
            request,
            "Room updated successfully.",
        )

        return redirect(
            "room_list",
        )

    return render(
        request,
        "rooms/room_form.html",
        {
            "room": room,
            "hotels": hotels,
            "categories": categories,
            "buildings": buildings,
            "floors": floors,
            "statuses": Room.STATUS_CHOICES,
        },
    )

@user_passes_test(manager_admin_or_director)
def room_detail(request, pk):

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
        hotel__in=get_accessible_hotels(
            request.user,
        ),
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

    hotels = (
        get_accessible_hotels(request.user)
        .filter(is_active=True)
        .order_by("name")
    )

    amenities = (
        Amenity.objects
        .filter(
            hotel__in=hotels,
        )
        .select_related("hotel")
        .order_by(
            "hotel__name",
            "name",
        )
    )

    search = request.GET.get("search", "").strip()
    hotel_id = request.GET.get("hotel", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        amenities = amenities.filter(
            Q(name__icontains=search)
            | Q(description__icontains=search)
        )

    if hotel_id:
        amenities = amenities.filter(
            hotel_id=hotel_id,
        )

    if status == "active":
        amenities = amenities.filter(
            is_active=True,
        )
    elif status == "inactive":
        amenities = amenities.filter(
            is_active=False,
        )

    return render(
        request,
        "rooms/amenity_list.html",
        {
            "amenities": amenities,
            "hotels": hotels,
        },
    )


@user_passes_test(manager_admin_or_director)
def amenity_create(request):

    hotels = (
        get_accessible_hotels(request.user)
        .filter(is_active=True)
        .order_by("name")
    )

    if not hotels.exists():
        messages.error(
            request,
            "You do not have access to any hotel.",
        )
        return redirect("owner_dashboard")

    if request.method == "POST":

        hotel_id = request.POST.get("hotel")

        name = request.POST.get(
            "name",
            "",
        ).strip()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )

        if not name:
            messages.error(
                request,
                "Amenity name is required.",
            )
            return redirect("amenity_create")

        if Amenity.objects.filter(
            hotel=hotel,
            name__iexact=name,
        ).exists():

            messages.error(
                request,
                "An amenity with this name already exists for this hotel.",
            )

            return redirect("amenity_create")

        Amenity.objects.create(
            hotel=hotel,
            name=name,
            description=description,
        )

        messages.success(
            request,
            f"Amenity '{name}' created.",
        )

        return redirect("amenity_list")

    return render(
        request,
        "rooms/amenity_form.html",
        {
            "hotels": hotels,
        },
    )


@user_passes_test(manager_admin_or_director)
def amenity_edit(request, pk):

    hotels = (
        get_accessible_hotels(request.user)
        .filter(is_active=True)
        .order_by("name")
    )

    amenity = get_object_or_404(
        Amenity.objects.select_related("hotel"),
        pk=pk,
        hotel__in=hotels,
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
                hotel=amenity.hotel,
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
                "An amenity with this name already exists for this hotel.",
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
            "hotels": hotels,
        },
    )


@user_passes_test(manager_admin_or_director)
def amenity_toggle_active(request, pk):

    hotels = get_accessible_hotels(
        request.user,
    )

    amenity = get_object_or_404(
        Amenity,
        pk=pk,
        hotel__in=hotels,
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

    hotels = get_accessible_hotels(
        request.user,
    )

    room = get_object_or_404(
        Room.objects
        .select_related(
            "category",
            "building",
            "floor",
            "hotel",
        )
        .prefetch_related(
            "room_amenities__amenity",
        ),
        pk=pk,
        hotel__in=hotels,
    )

    amenities = (
        Amenity.objects
        .filter(
            hotel=room.hotel,
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

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    hotels = (
        accessible_hotels
        .filter(is_active=True)
        .order_by("name")
    )

    buildings = (
        Building.objects
        .filter(
            hotel__in=accessible_hotels,
            is_active=True,
        )
        .select_related("hotel")
        .order_by(
            "hotel__name",
            "name",
        )
    )

    floors = (
        Floor.objects
        .filter(
            building__hotel__in=accessible_hotels,
        )
        .select_related(
            "building",
            "building__hotel",
        )
    )

    search = request.GET.get("search", "").strip()
    hotel_id = request.GET.get("hotel", "").strip()
    building_id = request.GET.get("building", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        floors = floors.filter(
            Q(building__name__icontains=search)
            | Q(name__icontains=search)
        )

    if hotel_id:
        floors = floors.filter(
            building__hotel_id=hotel_id,
        )

    if building_id:
        floors = floors.filter(
            building_id=building_id,
        )

    if status == "active":
        floors = floors.filter(
            is_active=True,
        )
    elif status == "inactive":
        floors = floors.filter(
            is_active=False,
        )

    floors = floors.order_by(
        "building__hotel__name",
        "building__name",
        "number",
    )

    return render(
        request,
        "rooms/floor_list.html",
        {
            "floors": floors,
            "hotels": hotels,
            "buildings": buildings,
        },
    )

@user_passes_test(manager_admin_or_director)
def floor_create(request):

    buildings = (
        Building.objects
        .filter(
            hotel__in=get_accessible_hotels(
                request.user,
            ),
            is_active=True,
        )
        .select_related(
            "hotel",
        )
        .order_by(
            "hotel__name",
            "name",
        )
    )

    selected_building_id = request.GET.get("building")

    if request.method == "POST":

        building_id = request.POST.get(
            "building",
        )

        name = request.POST.get(
            "name",
            "",
        ).strip()

        number = request.POST.get(
            "number",
            "",
        ).strip()

        if not building_id:
            messages.error(
                request,
                "Building is required.",
            )
            return redirect(
                "floor_create",
            )

        if not name:
            messages.error(
                request,
                "Floor name is required.",
            )
            return redirect(
                "floor_create",
            )

        try:
            number = int(number)
        except (TypeError, ValueError):

            messages.error(
                request,
                "Floor number must be a valid number.",
            )
            return redirect(
                "floor_create",
            )

        if number < 0:

            messages.error(
                request,
                "Floor number cannot be negative.",
            )
            return redirect(
                "floor_create",
            )

        building = get_object_or_404(
            buildings,
            pk=building_id,
        )

        if Floor.objects.filter(
            building=building,
            number=number,
        ).exists():

            messages.error(
                request,
                f"Floor {number} already exists in "
                f"{building.name}.",
            )

            return redirect(
                f"/manage/floors/add/?building={building.pk}"
            )

        Floor.objects.create(
            building=building,
            name=name,
            number=number,
        )

        messages.success(
            request,
            "Floor created.",
        )

        # If the user came from a specific building,
        # return them to that building's floor management page.
        if request.GET.get("building"):

            return redirect(
                "building_detail",
                pk=building.pk,
            )

        return redirect(
            "floor_list",
        )

    return render(
        request,
        "rooms/floor_form.html",
        {
            "buildings": buildings,
            "selected_building_id": selected_building_id,
        },
    )

@user_passes_test(manager_admin_or_director)
def floor_edit(request, pk):

    floor = get_object_or_404(
        Floor.objects.select_related(
            "building",
            "building__hotel",
        ),
        pk=pk,
        building__hotel__in=get_accessible_hotels(
            request.user,
        ),
    )

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        number = request.POST.get(
            "number",
            "",
        ).strip()

        if not name:
            messages.error(
                request,
                "Floor name is required.",
            )
            return redirect(
                "floor_edit",
                pk=floor.pk,
            )

        try:
            number = int(number)
        except (TypeError, ValueError):
            messages.error(
                request,
                "Floor number must be a valid number.",
            )
            return redirect(
                "floor_edit",
                pk=floor.pk,
            )

        if number < 0:
            messages.error(
                request,
                "Floor number cannot be negative.",
            )
            return redirect(
                "floor_edit",
                pk=floor.pk,
            )

        duplicate = (
            Floor.objects
            .filter(
                building=floor.building,
                number=number,
            )
            .exclude(
                pk=floor.pk,
            )
            .exists()
        )

        if duplicate:
            messages.error(
                request,
                f"Floor {number} already exists "
                f"in {floor.building.name}.",
            )
            return redirect(
                "floor_edit",
                pk=floor.pk,
            )

        floor.name = name
        floor.number = number

        floor.save(
            update_fields=[
                "name",
                "number",
            ],
        )

        messages.success(
            request,
            "Floor updated successfully.",
        )

        return redirect(
            "building_detail",
            pk=floor.building_id,
        )

    return render(
        request,
        "rooms/floor_form.html",
        {
            "floor": floor,
            "building": floor.building,
            "edit_mode": True,
        },
    )
@user_passes_test(manager_admin_or_director)
def floor_toggle_active(request, pk):

    if request.method != "POST":
        raise PermissionDenied

    floor = get_object_or_404(
        Floor.objects.select_related(
            "building",
            "building__hotel",
        ),
        pk=pk,
        building__hotel__in=get_accessible_hotels(
            request.user,
        ),
    )

    floor.is_active = not floor.is_active

    floor.save(
        update_fields=[
            "is_active",
        ],
    )

    state = (
        "activated"
        if floor.is_active
        else "deactivated"
    )

    messages.success(
        request,
        f"Floor '{floor.name}' {state}.",
    )

    return redirect(
        "building_detail",
        pk=floor.building_id,
    )

