from django.urls import path
from .views import hotel_dashboard

urlpatterns = [
    path("dashboard/", hotel_dashboard, name="hotel_dashboard"),
]
