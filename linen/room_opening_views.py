from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels

from .models import RoomLinenOpeningBatch, RoomLinenOpeningLine
from .services.room_opening import (
    create_room_linen_opening_batch,
    post_room_linen_opening_batch,
)

MANAGEMENT_ROLES = ("ADMIN", "DIRECTOR", "GENERAL_MANAGER", "MANAGER")


@role_required(*MANAGEMENT_ROLES)
def room_linen_opening(request):
    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied

    hotel_id = request.POST.get("hotel") if request.method == "POST" else request.GET.get("hotel")
    if hotel_id:
        hotel = get_object_or_404(hotels, pk=hotel_id)
    elif hotels.count() == 1:
        hotel = hotels.first()
    else:
        hotel = None

    if request.method == "POST":
        if hotel is None:
            messages.error(request, "Select a hotel first.")
            return redirect("linen_room_opening")

        action = request.POST.get("action")
        if action == "CREATE_BATCH":
            try:
                batch = create_room_linen_opening_batch(
                    hotel=hotel,
                    user=request.user,
                    reference=request.POST.get("reference", ""),
                    notes=request.POST.get("notes", ""),
                )
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect(f"{request.path}?hotel={hotel.pk}")
            messages.success(request, f"Opening batch {batch.reference} created.")
            return redirect(f"{request.path}?hotel={hotel.pk}&batch={batch.pk}")

        batch = get_object_or_404(
            RoomLinenOpeningBatch, pk=request.POST.get("batch_id"), hotel=hotel,
        )
        if action == "UPDATE_LINE":
            if batch.status != "DRAFT":
                messages.error(request, "Only draft opening batches can be edited.")
            else:
                line = get_object_or_404(
                    RoomLinenOpeningLine,
                    pk=request.POST.get("line_id"),
                    opening_room__batch=batch,
                )
                raw = request.POST.get("actual_quantity", "").strip()
                try:
                    quantity = int(raw)
                    if quantity < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    messages.error(request, "Enter a whole number of zero or greater.")
                else:
                    line.actual_quantity = quantity
                    line.save(update_fields=["actual_quantity"])
                    messages.success(request, "Room count updated.")
            return redirect(f"{request.path}?hotel={hotel.pk}&batch={batch.pk}")

        if action == "POST_BATCH":
            try:
                post_room_linen_opening_batch(batch=batch, user=request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, f"Room linen opening batch {batch.reference} posted.")
            return redirect(f"{request.path}?hotel={hotel.pk}&batch={batch.pk}")

    selected_batch = None
    if hotel and request.GET.get("batch"):
        selected_batch = get_object_or_404(
            RoomLinenOpeningBatch.objects.prefetch_related(
                "rooms__room", "rooms__lines__linen_item__product",
            ),
            pk=request.GET["batch"], hotel=hotel,
        )

    batches = (
        RoomLinenOpeningBatch.objects.filter(hotel=hotel).order_by("-created_at", "-id")
        if hotel else RoomLinenOpeningBatch.objects.none()
    )
    return render(request, "linen/room_opening.html", {
        "hotels": hotels,
        "selected_hotel": hotel,
        "batches": batches,
        "selected_batch": selected_batch,
    })
