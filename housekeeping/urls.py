from django.urls import path
from .views import dashboard, mark_clean

urlpatterns = [
    path("", dashboard, name="housekeeping_dashboard"),
    path("clean/<int:room_id>/", mark_clean, name="housekeeping_mark_clean"),
]
