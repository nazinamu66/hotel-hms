from django.urls import path

from . import views

urlpatterns = [
    path("operations/", views.salon_operations_dashboard, name="salon_operations_dashboard"),
    path("management/", views.salon_management_dashboard, name="salon_management_dashboard"),
    path("management/services/", views.salon_service_catalogue, name="salon_service_catalogue"),
    path("management/discount-policy/", views.salon_discount_policy, name="salon_discount_policy"),
    path("", views.salon_pos, name="salon_pos"),
    path("orders/create/", views.salon_order_create, name="salon_order_create"),
    path("orders/<int:order_id>/checkout/", views.salon_checkout, name="salon_checkout"),
    path("orders/<int:order_id>/settle/", views.salon_order_settle, name="salon_order_settle"),
    path("orders/<int:order_id>/receipt/", views.salon_receipt, name="salon_receipt"),
    path("customers/lookup/", views.salon_customer_lookup, name="salon_customer_lookup"),
]
