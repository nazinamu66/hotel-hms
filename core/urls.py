from django.urls import path

from .views import (
    hotel_dashboard,
    management_overview,
    management_hotel_performance,
    management_front_desk_analytics,
    cash_handover_list,
    cash_handover_accept,
    cash_custody_dashboard,
    cash_custody_detail,
    cash_custody_resolve,
)


urlpatterns = [
    path(
        "management/overview/",
        management_overview,
        name="management_overview",
    ),
    path(
        "management/hotels/",
        management_hotel_performance,
        name="management_hotel_performance",
    ),
    path(
        "management/front-desk/",
        management_front_desk_analytics,
        name="management_front_desk_analytics",
    ),

    path(
        "dashboard/",
        hotel_dashboard,
        name="hotel_dashboard",
    ),

    path(
        "cash-handovers/",
        cash_handover_list,
        name="cash_handover_list",
    ),

    path(
        "cash-handovers/<int:handover_id>/accept/",
        cash_handover_accept,
        name="cash_handover_accept",
    ),

    path(
        "cash-custody/",
        cash_custody_dashboard,
        name="cash_custody_dashboard",
    ),

    path(
        "cash-custody/<int:handover_id>/",
        cash_custody_detail,
        name="cash_custody_detail",
    ),

    path(
        "cash-custody/<int:handover_id>/resolve/",
        cash_custody_resolve,
        name="cash_custody_resolve",
    ),
]