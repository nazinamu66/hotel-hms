from django.urls import path

from .views import (
    dashboard,
    mark_clean,
    cleaning_history,
)

urlpatterns = [

    # Dashboard
    path("", dashboard, name="housekeeping_dashboard"),

    # Cleaning actions
    path("rooms/<int:room_id>/clean/", mark_clean, name="housekeeping_mark_clean"),

    # Cleaning history
    path("history/", cleaning_history, name="housekeeping_cleaning_history"),

]