from inventory.models import PurchaseOrder, LowStockRequest
from kitchen.models import IngredientRestockRequest, DirectPurchase
from maintenance.models import MaintenanceTicket
from django.urls import reverse, NoReverseMatch

from core.utils import get_user_hotels


def _url(name, **kwargs):
    """
    Safely resolve a URL.

    If a route is temporarily missing while we are building the
    notification system, return '#' rather than breaking the navbar.
    """
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        return "#"


def _hotel_ids(user):
    """
    Return all hotel IDs the user is authorized to access.

    Uses the same hotel-access rule as the rest of the ERP.
    """

    return list(
        get_user_hotels(user).values_list(
            "id",
            flat=True,
        )
    )


def get_pending_actions(user):
    """
    Return actionable pending items for the current user.

    This is an ACTION engine, not a generic notification history.

    Each returned item contains:

        type
        label
        count
        url
        section

    The caller/template decides how these are displayed.
    """

    role = getattr(user, "role", None)

    hotel_ids = _hotel_ids(user)

    if not hotel_ids:
        return []

    actions = []

    # ============================================================
    # MANAGEMENT APPROVALS
    # ============================================================

    if role in ("MANAGER", "DIRECTOR", "ADMIN"):

        kitchen_count = (
            IngredientRestockRequest.objects
            .filter(
                requested_by__department__hotel_id__in=hotel_ids,
                status="PENDING",
            )
            .exclude(requested_by=user)
            .count()
        )

        if kitchen_count:
            actions.append({
                "type": "KITCHEN_REQUESTS",
                "label": "Kitchen Requests",
                "count": kitchen_count,
                "url": _url("manager_ingredient_requests"),
                "section": "APPROVALS",
            })

        stock_count = (
            LowStockRequest.objects
            .filter(
                department__hotel_id__in=hotel_ids,
                fulfillment_type="PURCHASE",
                status="PENDING",
            )
            .count()
        )

        if stock_count:
            actions.append({
                "type": "STOCK_REQUESTS",
                "label": "Stock Requests",
                "count": stock_count,
                "url": _url("inventory:manager_stock_requests"),
                "section": "APPROVALS",
            })
    
    # ============================================================
    # STORE FULFILLMENT
    # ============================================================

    if role == "STORE":

        store_count = (
            LowStockRequest.objects
            .filter(
                department__hotel_id__in=hotel_ids,
                department__department_type__in=(
                    "HOUSEKEEPING",
                    "MAINTENANCE",
                ),
                fulfillment_type="STORE",
                status__in=(
                    "PENDING",
                    "PARTIALLY_FULFILLED",
                ),
            )
            .count()
        )

        if store_count:
            actions.append({
                "type": "STORE_STOCK_REQUESTS",
                "label": "Stock Requests",
                "count": store_count,
                "url": _url("store_stock_requests"),
                "section": "STORE",
            })
    # ============================================================
    # MAINTENANCE / OPERATIONS
    # ============================================================

    if role == "MAINTENANCE":

        maintenance_count = (
            MaintenanceTicket.objects
            .filter(
                room__hotel_id__in=hotel_ids,
                status="OPEN",
            )
            .count()
        )

        if maintenance_count:
            actions.append({
                "type": "MAINTENANCE_TICKETS",
                "label": "Maintenance Tickets",
                "count": maintenance_count,
                "url": _url("maintenance_dashboard"),
                "section": "OPERATIONS",
            })

    elif role in ("MANAGER", "DIRECTOR", "ADMIN"):

        maintenance_count = (
            MaintenanceTicket.objects
            .filter(
                room__hotel_id__in=hotel_ids,
                status__in=("OPEN", "IN_PROGRESS"),
            )
            .count()
        )

        if maintenance_count:
            actions.append({
                "type": "MAINTENANCE_TICKETS",
                "label": "Maintenance Tickets",
                "count": maintenance_count,
                "url": _url("maintenance_dashboard"),
                "section": "OPERATIONS",
            })
    # ============================================================
    # ACCOUNTING / PAYMENT
    # ============================================================

    if role in ("ACCOUNTANT", "DIRECTOR", "ADMIN"):

        po_count = (
            PurchaseOrder.objects 
            .filter(
                department__hotel_id__in=hotel_ids,
                status="SUBMITTED",
            )
            .count()
        )

        if po_count:
            actions.append({
                "type": "PURCHASE_ORDERS_PAYMENT",
                "label": "Purchase Orders",
                "count": po_count,
                "url": _url("inventory:po_list"),
                "section": "PAYMENTS",
            })

        direct_count = (
            DirectPurchase.objects
            .filter(
                ingredient_request__requested_by__department__hotel_id__in=hotel_ids,
                status="APPROVED",
            )
            .count()
        )

        if direct_count:
            actions.append({
                "type": "DIRECT_PURCHASE_PAYMENT",
                "label": "Direct Purchases",
                "count": direct_count,
                "url": _url("direct_purchase_list"),
                "section": "PAYMENTS",
            })

    return actions