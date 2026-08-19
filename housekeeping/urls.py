from django.urls import path

from .views import (
    dashboard,
    mark_clean,
    cleaning_history,
    assign_room,
    lost_found_create,
    lost_found_list,
    start_cleaning,
    finish_cleaning,
    approve_cleaning,
)

urlpatterns = [

    # Dashboard
    path(
        "",
        dashboard,
        name="housekeeping_dashboard",
    ),

    # Legacy cleaning action
    path(
        "rooms/<int:room_id>/clean/",
        mark_clean,
        name="housekeeping_mark_clean",
    ),

    # Cleaning history
    path(
        "history/",
        cleaning_history,
        name="housekeeping_cleaning_history",
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
]