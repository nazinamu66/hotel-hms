from django.urls import path
from . import views
from inventory.views import (
    department_request_stock,
    department_stock_requests,
)

urlpatterns = [
    path("", views.store_dashboard, name="store_dashboard"),
    path(
        "stock-requests/",
        views.store_stock_requests,
        name="store_stock_requests",
    ),

    # ============================================================
    # LINEN
    # ============================================================

    path(
        "linen-requests/",
        views.store_linen_requests,
        name="store_linen_requests",
    ),

    path(
        "linen-requests/<int:pk>/fulfill/",
        views.store_fulfill_linen_request,
        name="store_fulfill_linen_request",
    ),
    
    path(
        "stock-requests/<int:pk>/issue/",
        views.store_issue_stock_request,
        name="store_issue_stock_request",
    ),
    path("issue/", views.issue_stock, name="store_issue_stock"),

    path(
        "movements/",
        views.stock_movement_log,
        name="store_movement_log",
    ),

    path(
        "requests/<int:pk>/",
        views.stock_request_detail,
        name="store_request_detail",
    ),

    path(
        "transfers/<int:pk>/",
        views.transfer_detail,
        name="store_transfer_detail",
    ),

    path(
        "ingredient-requests/",
        views.store_ingredient_requests,
        name="store_ingredient_requests",
    ),

    path(
        "requests/item/<int:item_id>/issue/",
        views.issue_request_item,
        name="issue_request_item",
    ),

    path(
        "history/issues/",
        views.store_issue_history,
        name="store_issue_history",
    ),

    path(
        "history/receipts/",
        views.store_receive_history,
        name="store_receive_history",
    ),

    # Generic department stock requests
    path(
        "request/",
        department_request_stock,
        name="store_request_low_stock",
    ),

    path(
        "requests/",
        department_stock_requests,
        name="store_requests",
    ),

]
