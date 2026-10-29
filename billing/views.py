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
from accounting.models import BankAccount
from accounting.services.journal import get_system_account
from billing.workflows.refunds import (
    approve_refund_request,
    create_refund_request,
    execute_refund_request,
    confirm_kitchen_refund,
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


def execute_refund(request, request_id):
    refund_request = get_object_or_404(
        RefundRequest.objects.select_related(
            "pos_order__department__hotel",
            "charge__folio__hotel",
            "execution_account",
        ),
        id=request_id,
    )
    hotel = _refund_hotel(refund_request)
    if not user_can_access_hotel(request.user, hotel):
        raise PermissionDenied
    if refund_request.status != "APPROVED":
        messages.error(request, "Only approved refund requests can be executed.")
        return redirect("refund_list")

    order = refund_request.pos_order if refund_request.source_type == "POS_ORDER" else None
    is_paid_pos = bool(order and order.status == "PAID")
    bank_accounts = []
    cash_account = None
    if order and is_paid_pos:
        bank_accounts = BankAccount.objects.filter(
            hotel=hotel, is_active=True, account__is_active=True,
            account__allow_posting=True,
        ).select_related("account").order_by("bank_name", "account_name")
        try:
            cash_account = get_system_account(hotel, "cash")
        except (ValueError, Exception):
            cash_account = None

    if request.method == "GET":
        return render(request, "billing/refund_execute.html", {
            "refund": refund_request, "order": order, "is_paid_pos": is_paid_pos,
            "bank_accounts": bank_accounts, "cash_account": cash_account,
        })

    if order:
        method = request.POST.get("execution_method", "").strip()
        account_id = request.POST.get("execution_account", "").strip()
        reference = request.POST.get("execution_reference", "").strip()
        note = request.POST.get("execution_note", "").strip()
        external_confirmed = request.POST.get("external_refund_confirmed") == "on"
        try:
            if is_paid_pos:
                if method not in {"CASH", "POS", "TRANSFER"}:
                    raise ValidationError("Choose how the refund will be paid.")
                if method == "CASH":
                    if not cash_account:
                        raise ValidationError("The hotel's Cash account is not configured.")
                    payout_account = cash_account
                else:
                    bank_account = BankAccount.objects.filter(
                        pk=account_id, hotel=hotel, is_active=True,
                        account__is_active=True, account__allow_posting=True,
                    ).select_related("account").first()
                    if not bank_account:
                        raise ValidationError("Choose an active bank account belonging to this hotel.")
                    payout_account = bank_account.account
                if method == "POS" and not external_confirmed:
                    raise ValidationError("Confirm that the POS terminal/provider reversal has actually been completed.")
                if method in {"POS", "TRANSFER"} and not reference:
                    raise ValidationError("Enter the POS reversal or bank transfer reference.")
            else:
                method, payout_account, external_confirmed = "ROOM_REVERSAL", None, False

            refund_request.execution_method = method
            refund_request.execution_account = payout_account
            refund_request.execution_reference = reference
            refund_request.execution_note = note
            refund_request.external_refund_confirmed = external_confirmed
            refund_request.save(update_fields=[
                "execution_method", "execution_account", "execution_reference",
                "execution_note", "external_refund_confirmed",
            ])
            execute_refund_request(refund_request, request.user)
            if refund_request.status == "KITCHEN_CONFIRMATION":
                messages.success(request, f"Director authorization recorded for refund #{refund_request.id}; Kitchen confirmation is still required.")
            else:
                messages.success(request, f"Refund request #{refund_request.id} completed.")
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
            return render(request, "billing/refund_execute.html", {
                "refund": refund_request, "order": order, "is_paid_pos": is_paid_pos,
                "bank_accounts": bank_accounts, "cash_account": cash_account,
                "form_error": str(exc),
            })
        return redirect("refund_list")

    try:
        execute_refund_request(refund_request, request.user)
        messages.success(request, f"Refund request #{refund_request.id} completed.")
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("refund_list")


@role_required("KITCHEN")
def kitchen_refund_queue(request):
    department = request.user.department
    if not department or department.department_type != "KITCHEN":
        raise PermissionDenied("You must be assigned to a Kitchen department.")

    refunds = RefundRequest.objects.filter(
        source_type="POS_ORDER",
        status="KITCHEN_CONFIRMATION",
        pos_order__department__hotel_id=department.hotel_id,
    ).select_related(
        "pos_order__department__hotel",
        "director_authorized_by",
        "requested_by",
    ).prefetch_related("pos_order__items__menu_item__product").order_by("director_authorized_at")

    return render(request, "billing/kitchen_refund_queue.html", {"refund_requests": refunds})


@role_required("KITCHEN")
@require_POST
def confirm_kitchen_refund_view(request, request_id):
    department = request.user.department
    if not department or department.department_type != "KITCHEN":
        raise PermissionDenied("You must be assigned to a Kitchen department.")

    refund_request = get_object_or_404(
        RefundRequest.objects.select_related("pos_order__department__hotel"),
        pk=request_id,
        source_type="POS_ORDER",
        status="KITCHEN_CONFIRMATION",
        pos_order__department__hotel_id=department.hotel_id,
    )
    try:
        confirm_kitchen_refund(
            refund_request,
            request.user,
            request.POST.get("kitchen_outcome", ""),
            request.POST.get("kitchen_confirmation_note", ""),
        )
        messages.success(request, f"Refund #{refund_request.pk} confirmed and completed.")
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("kitchen_refund_queue")
