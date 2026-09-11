from django.urls import path

from .views import (
    dashboard,
    cleaning_history,
    assign_room,
    lost_found_create,
    lost_found_list,
    start_cleaning,
    finish_cleaning,
    approve_cleaning,
    lost_found_claim,
    lost_found_dispose,
    cleaning_material_add,
    housekeeping_stock,
    housekeeping_stock_movements,
    request_new_linen,
    housekeeping_linen_requests,
    send_linen_to_laundry,
    housekeeping_linen_movements,
    cleaning_linen_check,
    request_laundry_linen
)
from inventory.views import (
    department_request_stock,
    department_stock_requests,
    department_incoming_pos,
)
urlpatterns = [

    # Dashboard
    path(
        "",
        dashboard,
        name="housekeeping_dashboard",
    ),

    

    # Cleaning history
    path(
        "history/",
        cleaning_history,
        name="housekeeping_cleaning_history",
    ),

    path(
        "cleaning/<int:assignment_id>/materials/",
        cleaning_material_add,
        name="housekeeping_cleaning_materials",
    ),

    # Assignment
    path(
        "rooms/<int:room_id>/assign/",
        assign_room,
        name="housekeeping_assign_room",
    ),

    # Cleaning lifecycle
    path(
        "rooms/<int:room_id>/start-cleaning/",
        start_cleaning,
        name="housekeeping_start_cleaning",
    ),

    path(
        "rooms/<int:room_id>/finish-cleaning/",
        finish_cleaning,
        name="housekeeping_finish_cleaning",
    ),

    path(
        "rooms/<int:room_id>/approve-cleaning/",
        approve_cleaning,
        name="housekeeping_approve_cleaning",
    ),

    # Lost & Found
    path(
        "lost-found/",
        lost_found_list,
        name="housekeeping_lost_found",
    ),

    path(
        "lost-found/create/",
        lost_found_create,
        name="housekeeping_lost_found_create",
    ),

    path(
        "lost-found/<int:item_id>/claim/",
        lost_found_claim,
        name="housekeeping_lost_found_claim",
    ),

    path(
        "lost-found/<int:item_id>/dispose/",
        lost_found_dispose,
        name="housekeeping_lost_found_dispose",
    ),

    # ============================================================
    # HOUSEKEEPING STOCK
    # ============================================================

    path(
        "stock/request/",
        department_request_stock,
        name="housekeeping_request_stock",
    ),

    path(
        "stock/requests/",
        department_stock_requests,
        name="housekeeping_stock_requests",
    ),

    path(
        "stock/incoming/",
        department_incoming_pos,
        name="housekeeping_incoming_pos",
    ),
    path(
        "stock/",
        housekeeping_stock,
        name="housekeeping_stock",
    ),
    path(
        "stock/movements/",
        housekeeping_stock_movements,
        name="housekeeping_stock_movements",
    ),

    # ============================================================
    # LINEN
    # ============================================================

    path(
        "linen/request/",
        request_new_linen,
        name="housekeeping_request_new_linen",
    ),

    path(
        "linen/request-laundry/",
        request_laundry_linen,
        name="housekeeping_request_laundry",
    ),

    path(
        "linen/requests/",
        housekeeping_linen_requests,
        name="housekeeping_linen_requests",
    ),

    path(
        "linen/send-to-laundry/",
        send_linen_to_laundry,
        name="housekeeping_send_linen_to_laundry",
    ),

    path(
        "linen/movements/",
        housekeeping_linen_movements,
        name="housekeeping_linen_movements",
    ),

    path(
        "cleaning/<int:assignment_id>/linen/",
        cleaning_linen_check,
        name="housekeeping_cleaning_linen",
    ),

]