from django.urls import path

from .views import (
    dashboard,
    mark_clean,
    cleaning_history,
    assign_room,
    lost_found_create,
    lost_found_list,
)

urlpatterns = [

    # Dashboard
    path("", dashboard, name="housekeeping_dashboard"),

    # Cleaning actions
    path("rooms/<int:room_id>/clean/", mark_clean, name="housekeeping_mark_clean"),

    # Cleaning history
    path("history/", cleaning_history, name="housekeeping_cleaning_history"),
    
    # Assignment
    path("rooms/<int:room_id>/assign/",assign_room,name="housekeeping_assign_room"),

    path("lost-found/",lost_found_list,name="housekeeping_lost_found"),
    path("lost-found/create/",lost_found_create,name="housekeeping_lost_found_create"),

]