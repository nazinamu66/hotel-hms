from django.urls import path
from .views import restaurant_daily_report, restaurant_end_of_shift

urlpatterns = [
    path("restaurant/daily/", restaurant_daily_report, name="restaurant_daily_report"),
    path("restaurant/end-of-shift/",restaurant_end_of_shift,name="restaurant_end_of_shift"),
]
