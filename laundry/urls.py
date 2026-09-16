from django.urls import path
from inventory.views import (
    department_request_stock,
    department_stock_requests,
)

from . import views


app_name = "laundry"


urlpatterns = [
    path(
        "",
        views.dashboard,
        name="dashboard",
    ),

    path(
        "guest/",
        views.guest_laundry,
        name="guest_laundry",
    ),

    path(
        "guest/new/",
        views.guest_laundry_new,
        name="guest_laundry_new",
    ),

    path(
        "guest/<int:order_id>/receive/",
        views.guest_laundry_receive,
        name="guest_laundry_receive",
    ),
    path("services/", views.laundry_service_list, name="service_list"),
    path("services/new/", views.laundry_service_create, name="service_create"),
    path("services/<int:pk>/edit/", views.laundry_service_edit, name="service_edit"),
    path("services/<int:pk>/archive/", views.laundry_service_archive, name="service_archive"),

    path(
        "guest/<int:order_id>/ready/",
        views.guest_laundry_ready,
        name="guest_laundry_ready",
    ),

    path(
        "guest/<int:order_id>/deliver/",
        views.guest_laundry_deliver,
        name="guest_laundry_deliver",
    ),
    
    path(
        "guest/<int:order_id>/payment/",
        views.guest_laundry_payment,
        name="guest_laundry_payment",
    ),

    path(
        "guest/<int:order_id>/receipt/",
        views.guest_laundry_receipt,
        name="guest_laundry_receipt",
    ),

    path(
        "guest/<int:order_id>/receipt/thermal/",
        views.guest_laundry_receipt_thermal,
        name="guest_laundry_receipt_thermal",
    ),

    path(
        "guest/<int:order_id>/",
        views.guest_laundry_detail,
        name="guest_laundry_detail",
    ),

    path(
        "guest/history/",
        views.guest_laundry_history,
        name="guest_laundry_history",
    ),

    path(
        "guest/<int:order_id>/cancel/",
        views.guest_laundry_cancel,
        name="guest_laundry_cancel",
    ),

    path(
        "guest/<int:order_id>/process/",
        views.guest_laundry_process,
        name="guest_laundry_process",
    ),

    path(
        "requests/",
        views.linen_requests,
        name="linen_requests",
    ),

    path(
        "requests/<int:request_id>/approve/",
        views.approve_linen_request_view,
        name="approve_linen_request",
    ),

    path(
        "requests/<int:request_id>/fulfill/",
        views.fulfill_linen_request_view,
        name="fulfill_linen_request",
    ),

    path(
        "process/",
        views.process_linen,
        name="process_linen",
    ),

    path(
        "receipts/<int:receipt_id>/",
        views.receipt_detail,
        name="receipt_detail",
    ),

    path(
        "receipts/<int:receipt_id>/receive/",
        views.receive_receipt,
        name="receive_receipt",
    ),

    path(
        "stock/",
        views.laundry_stock,
        name="stock",
    ),

    path(
        "guest-laundry/<int:order_id>/materials/",
        views.guest_laundry_materials,
        name="guest_laundry_materials",
    ),
    
    path(
        "stock/request/",
        department_request_stock,
        name="laundry_request_stock",
    ),
    path(
        "stock/requests/",
        department_stock_requests,
        name="laundry_stock_requests",
    ),
]