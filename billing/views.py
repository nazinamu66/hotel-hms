from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.services.access import (
    get_accessible_hotels,
    user_can_access_department,
    user_can_access_hotel,
)
from accounts.services.decorators import role_required
from billing.models import Charge, RefundRequest
from billing.workflows.refunds import (
    approve_refund_request,
    create_refund_request,
    execute_refund_request,
    reject_refund_request,
)
from restaurant.models import POSOrder



def _refund_hotel(refund_request):
    if refund_request.source_type == "POS_ORDER":
        return refund_request.pos_order.department.hotel

    if refund_request.source_type == "CHARGE":
        return refund_request.charge.folio.hotel

    return None


@role_required("RESTAURANT")

def request_pos_refund(request, order_id):
    order = get_object_or_404(
        POSOrder.objects.select_related(
            "department__hotel",
        ),
        id=order_id,
    )

    # if request.user.role != "RESTAURANT":
    #     raise PermissionDenied(
    #         "Only Restaurant staff can request POS refunds."
    #     )

    if not user_can_access_department(
        request.user,
        order.department,
    ):
        raise PermissionDenied

    if request.method != "POST":
        return render(
            request,
            "billing/refund_request.html",
            {
                "source": order,
                "source_type": "POS_ORDER",
            },
        )

    reason = request.POST.get(
        "reason",
        "",
    )

    try:
        refund_request = create_refund_request(
            source_type="POS_ORDER",
            requested_by=request.user,
            reason=reason,
            pos_order=order,
        )

        messages.success(
            request,
            f"Refund request #{refund_request.id} submitted.",
        )

        return redirect(
            "restaurant_order_detail",
            order_id=order.id,
        )

    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            str(exc),
        )

    return render(
        request,
        "billing/refund_request.html",
        {
            "source": order,
            "source_type": "POS_ORDER",
        },
    )


@role_required("FRONTDESK")
def request_charge_refund(
    request,
    charge_id,
):
    charge = get_object_or_404(
        Charge.objects.select_related(
            "department__hotel",
            "folio__hotel",
        ),
        id=charge_id,
    )

    if not user_can_access_hotel(
        request.user,
        charge.folio.hotel,
    ):
        raise PermissionDenied

    if request.method != "POST":
        return render(
            request,
            "billing/refund_request.html",
            {
                "source": charge,
                "source_type": "CHARGE",
            },
        )

    reason = request.POST.get(
        "reason",
        "",
    )

    try:
        refund_request = create_refund_request(
            source_type="CHARGE",
            requested_by=request.user,
            reason=reason,
            charge=charge,
        )

        messages.success(
            request,
            f"Refund request #{refund_request.id} submitted.",
        )

        return redirect(
            "frontdesk_invoice",
            folio_id=charge.folio_id,
        )

    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            str(exc),
        )

    return render(
        request,
        "billing/refund_request.html",
        {
            "source": charge,
            "source_type": "CHARGE",
        },
    )


@role_required(
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def refund_list(request):
    hotels = get_accessible_hotels(request.user)

    requests = (
        RefundRequest.objects
        .select_related(
            "requested_by",
            "approved_by",
            "executed_by",
            "pos_order__department__hotel",
            "charge__department__hotel",
            "charge__folio",
        )
        .order_by("-requested_at")
    )

    visible = []

    for refund_request in requests:
        hotel = _refund_hotel(refund_request)

        if hotel and hotel.id in hotels.values_list(
            "id",
            flat=True,
        ):
            visible.append(refund_request)

    return render(
        request,
        "billing/refund_list.html",
        {
            "refund_requests": visible,
        },
    )


@role_required(
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
@require_POST
def approve_refund(request, request_id):
    refund_request = get_object_or_404(
        RefundRequest,
        id=request_id,
    )

    hotel = _refund_hotel(refund_request)

    if not user_can_access_hotel(
        request.user,
        hotel,
    ):
        raise PermissionDenied

    try:
        approve_refund_request(
            refund_request,
            request.user,
        )

        messages.success(
            request,
            f"Refund request #{refund_request.id} approved.",
        )

    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            str(exc),
        )

    return redirect("refund_list")


@role_required(
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
@require_POST
def reject_refund(request, request_id):
    refund_request = get_object_or_404(
        RefundRequest,
        id=request_id,
    )

    hotel = _refund_hotel(refund_request)

    if not user_can_access_hotel(
        request.user,
        hotel,
    ):
        raise PermissionDenied

    rejection_reason = request.POST.get(
        "rejection_reason",
        "",
    )

    try:
        reject_refund_request(
            refund_request,
            request.user,
            rejection_reason,
        )

        messages.success(
            request,
            f"Refund request #{refund_request.id} rejected.",
        )

    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            str(exc),
        )

    return redirect("refund_list")


@role_required(
    "DIRECTOR",
    "ADMIN",
)
@require_POST
def execute_refund(request, request_id):
    refund_request = get_object_or_404(
        RefundRequest,
        id=request_id,
    )

    hotel = _refund_hotel(refund_request)

    if not user_can_access_hotel(
        request.user,
        hotel,
    ):
        raise PermissionDenied

    try:
        execute_refund_request(
            refund_request,
            request.user,
        )

        messages.success(
            request,
            f"Refund request #{refund_request.id} completed.",
        )

    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            str(exc),
        )

    return redirect("refund_list")
