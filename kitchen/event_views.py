from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, render

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels
from billing.models import EventBooking
from .services.event_ingredients import build_event_ingredient_plan


@role_required("KITCHEN", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def kitchen_event_ingredient_plan(request, event_id):
    kitchen = request.user.department
    if request.user.role == "KITCHEN":
        if not kitchen or kitchen.department_type != "KITCHEN":
            raise PermissionDenied("You must be assigned to a Kitchen department.")
        hotel_scope = [kitchen.hotel_id]
    else:
        hotel_scope = get_accessible_hotels(request.user).values_list("pk", flat=True)

    event = get_object_or_404(
        EventBooking.objects.prefetch_related("order_lines__recipe__active_version"),
        pk=event_id,
        hotel_id__in=hotel_scope,
    )

    if request.user.role != "KITCHEN":
        kitchen = event.hotel.departments.filter(
            department_type="KITCHEN",
            is_active=True,
        ).first()
    if not kitchen:
        raise PermissionDenied("No active Kitchen department is configured for this hotel.")

    try:
        rows = build_event_ingredient_plan(event, kitchen)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        rows = []

    return render(request, "kitchen/events/ingredient_plan.html", {
        "event": event,
        "kitchen": kitchen,
        "rows": rows,
        "total_lines": len(rows),
        "physical_shortage_count": sum(1 for row in rows if row["physical_shortage"] > 0),
        "fifo_shortage_count": sum(1 for row in rows if row["fifo_shortage"] > 0),
    })
