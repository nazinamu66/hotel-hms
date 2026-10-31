from django.urls import path
from .views import opening_stock_dashboard

app_name = "opening_stock"

urlpatterns = [
    path("", opening_stock_dashboard, name="dashboard"),
]
