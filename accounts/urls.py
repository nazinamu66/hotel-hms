from django.urls import path
from . import views
from .views import (
    CustomLoginView,
    logout_view,
    role_redirect,
    manager_payments_today,
    manager_restaurant_orders_today,
    manager_room_activity_today,
    user_edit,
)

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', logout_view, name='logout'),
    path('redirect/', role_redirect, name='role_redirect'),

    path("users/", views.user_list, name="accounts_users"),
    path("users/create/", views.user_create, name="accounts_user_create"),
    path("manager/restaurant-orders/", manager_restaurant_orders_today),
    path("manager/room-activity/", manager_room_activity_today),
    path("manager/payments/", manager_payments_today),
    path("users/<int:user_id>/edit/",user_edit,name="accounts_user_edit"),
    path("organization/profile/", views.organization_profile, name="organization_profile"),
    path("setup/",views.initial_setup,name="initial_setup",),
    path("users/<int:user_id>/toggle-active/",views.user_toggle_active,name="accounts_user_toggle_active",),

]
