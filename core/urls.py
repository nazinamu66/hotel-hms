from django.urls import path

from .views import (
    hotel_dashboard,
    management_overview,
    management_custom_reports,
    management_hotel_performance,
    management_front_desk_analytics,
    management_front_desk_drilldown,
    management_guest_search,
    management_guest_profile,
    management_reservation_detail,
    management_reservations_occupancy,
    management_corporate_performance,
    management_company_detail,
    management_guest_company_billing,
    cash_handover_list,
    cash_handover_accept,
    cash_custody_dashboard,
    cash_custody_detail,
    cash_custody_resolve,
)


urlpatterns = [
    path(
        "management/custom-reports/",
        management_custom_reports,
        name="management_custom_reports",
    ),
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
        "management/guests/",
        management_guest_search,
        name="management_guest_search",
    ),
    path(
        "management/guests/<int:guest_id>/",
        management_guest_profile,
        name="management_guest_profile",
    ),
    path(
        "management/reservations/<str:record_type>/<int:record_id>/",
        management_reservation_detail,
        name="management_reservation_detail",
    ),
    path(
        "management/front-desk/<str:metric>/",
        management_front_desk_drilldown,
        name="management_front_desk_drilldown",
    ),
    path(
        "management/front-desk/",
        management_front_desk_analytics,
        name="management_front_desk_analytics",
    ),
    path(
        "management/reservations-occupancy/",
        management_reservations_occupancy,
        name="management_reservations_occupancy",
    ),
    path(
        "management/guest-company-billing/",
        management_guest_company_billing,
        name="management_guest_company_billing",
    ),
    path(
        "management/companies/<int:company_id>/",
        management_company_detail,
        name="management_company_detail",
    ),
    path(
        "management/corporate/",
        management_corporate_performance,
        name="management_corporate_performance",
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