from django.urls import path
from .views import kitchen_production, kitchen_stock_dashboard

urlpatterns = [
    path("produce/", kitchen_production, name="kitchen_produce"),
    path("stock/", kitchen_stock_dashboard, name="kitchen_stock"),
]
