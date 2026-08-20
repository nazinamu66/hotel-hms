from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from accounts.decorators import manager_admin_or_director
from django.db import transaction
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
def building_create(request):

    hotels = get_accessible_hotels(
        request.user,
    )

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

        Building.objects.create(
            hotel=hotel,
            name=name,
        )

        messages.success(
            request,
            f"Building '{name}' created.",
        )

        return redirect(
            "building_list",
        )

    return render(
        request,
        "rooms/building_form.html",
        {
            "hotels": hotels,
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

    floors = (
        Floor.objects
        .filter(
            building__hotel__in=get_accessible_hotels(
                request.user,
            ),
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

    return render(
        request,
        "rooms/floor_list.html",
        {
            "floors": floors,
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
                "floor_create",
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

        return redirect(
            "floor_list",
        )

    return render(
        request,
        "rooms/floor_form.html",
        {
            "buildings": buildings,
        },
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