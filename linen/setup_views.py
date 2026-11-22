from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels
from inventory.models import Product
from rooms.models import Room, RoomCategory

from .models import LinenItem, RoomLinenRequirement, RoomLinenState, RoomLinenSetup


MANAGEMENT_ROLES = ("ADMIN", "DIRECTOR", "GENERAL_MANAGER", "MANAGER")


def _accessible_linen_items(user):
    hotels = get_accessible_hotels(user)
    return LinenItem.objects.filter(
        product__hotel__in=hotels,
        is_active=True,
        product__is_active=True,
    ).select_related("product", "product__hotel").order_by("product__name")


@role_required(*MANAGEMENT_ROLES)
def linen_category_setup(request):
    """Maintain reusable linen defaults for each room category."""
    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied

    categories = RoomCategory.objects.filter(hotel__in=hotels, is_active=True).order_by("hotel__name", "name")
    linen_items = _accessible_linen_items(request.user)
    selected_id = request.GET.get("category") or request.POST.get("category")
    selected_category = None
    if selected_id:
        selected_category = get_object_or_404(categories, pk=selected_id)

    if request.method == "POST":
        if not selected_category:
            messages.error(request, "Please select a room category.")
            return redirect("linen_category_setup")

        # Validate the entire submission before writing anything. Returning a
        # redirect from inside atomic() does not roll back earlier writes.
        submitted_quantities = {}
        for item in linen_items:
            raw = request.POST.get(f"quantity_{item.pk}", "").strip()
            if raw == "":
                submitted_quantities[item.pk] = None
                continue
            try:
                quantity = int(raw)
            except (TypeError, ValueError):
                messages.error(request, f"Enter a whole number for {item.product.name}.")
                return redirect(f"{request.path}?category={selected_category.pk}")
            if quantity < 0:
                messages.error(request, f"Quantity for {item.product.name} cannot be negative.")
                return redirect(f"{request.path}?category={selected_category.pk}")
            submitted_quantities[item.pk] = quantity or None

        with transaction.atomic():
            for item in linen_items:
                quantity = submitted_quantities[item.pk]
                if quantity is None:
                    RoomLinenRequirement.objects.filter(
                        category=selected_category, linen_item=item
                    ).delete()
                    continue
                RoomLinenRequirement.objects.update_or_create(
                    category=selected_category,
                    linen_item=item,
                    defaults={"quantity": quantity},
                )

        messages.success(request, f"Linen defaults saved for {selected_category.name}.")
        return redirect(f"{request.path}?category={selected_category.pk}")

    defaults = {}
    if selected_category:
        defaults = {
            row.linen_item_id: row.quantity
            for row in RoomLinenRequirement.objects.filter(
                category=selected_category,
                linen_item__in=linen_items,
            )
        }

    item_rows = [
        {"item": item, "quantity": defaults.get(item.pk, "")}
        for item in linen_items
    ]

    return render(request, "linen/category_setup.html", {
        "categories": categories,
        "selected_category": selected_category,
        "linen_items": linen_items,
        "defaults": defaults,
        "item_rows": item_rows,
    })


@role_required(*MANAGEMENT_ROLES)
def linen_room_setup(request, pk):
    """Read-only room linen view; physical opening counts are posted in batches."""
    room = get_object_or_404(
        Room.objects.select_related("hotel", "category"),
        pk=pk,
        hotel__in=get_accessible_hotels(request.user),
    )
    linen_items = _accessible_linen_items(request.user).filter(product__hotel=room.hotel)

    if request.method == "POST":
        messages.error(
            request,
            "Room opening quantities are managed through the Room Linen Opening batch.",
        )
        return redirect("linen_room_setup", pk=room.pk)

    setup = RoomLinenSetup.objects.filter(room=room).first()
    states = {
        state.linen_item_id: state.quantity
        for state in RoomLinenState.objects.filter(
            room=room, linen_item__in=linen_items,
        )
    }
    category_defaults = {
        row.linen_item_id: row.quantity
        for row in RoomLinenRequirement.objects.filter(
            category=room.category, linen_item__in=linen_items,
        )
    }
    item_rows = [
        {
            "item": item,
            "state_quantity": states.get(item.pk, ""),
            "default_quantity": category_defaults.get(item.pk, ""),
        }
        for item in linen_items
    ]
    return render(request, "linen/room_setup.html", {
        "room": room,
        "linen_items": linen_items,
        "states": states,
        "category_defaults": category_defaults,
        "item_rows": item_rows,
        "setup": setup,
    })
