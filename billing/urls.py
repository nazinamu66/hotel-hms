from django.urls import path

from billing import views


urlpatterns = [
    path(
        "refunds/",
        views.refund_list,
        name="refund_list",
    ),
    path(
        "refunds/request/pos/<int:order_id>/",
        views.request_pos_refund,
        name="request_pos_refund",
    ),
    path(
        "refunds/request/charge/<int:charge_id>/",
        views.request_charge_refund,
        name="request_charge_refund",
    ),
    path(
        "refunds/<int:request_id>/approve/",
        views.approve_refund,
        name="approve_refund",
    ),
    path(
        "refunds/<int:request_id>/reject/",
        views.reject_refund,
        name="reject_refund",
    ),
    path(
        "refunds/<int:request_id>/execute/",
        views.execute_refund,
        name="execute_refund",
    ),
]
