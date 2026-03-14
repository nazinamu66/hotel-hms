from django.urls import path
from . import views
from .views import (
    building_list,
    building_create,
    building_toggle_active,
    category_list,
    category_create,
    category_edit
)

urlpatterns = [

    # Buildings
    path("buildings/", building_list, name="building_list"),
    path("buildings/add/", building_create, name="building_create"),
    path("buildings/<int:pk>/toggle/", building_toggle_active, name="building_toggle"),

    # Floors
    path("floors/", views.floor_list, name="floor_list"),
    path("floors/add/", views.floor_create, name="floor_create"),

    # Room Categories
    path("categories/", category_list, name="category_list"),
    path("categories/new/", category_create, name="category_create"),
    path("categories/<int:pk>/edit/", category_edit, name="category_edit"),

    # Rooms
    path("rooms/", views.room_list, name="room_list"),
    path("rooms/add/", views.room_create, name="room_create"),
    path("rooms/<int:pk>/edit/", views.room_edit, name="room_edit"),

    # Rates
    path("rates/", views.rate_list, name="rate_list"),
    path("rates/create/", views.rate_create, name="rate_create"),
]