from django.urls import path
from . import views
from .views import (
    CustomLoginView,
    logout_view,
    role_redirect,
    manager_dashboard,
    admin_dashboard,
    manager_payments_today,
    manager_restaurant_orders_today,
    manager_room_activity_today,
)

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', logout_view, name='logout'),
    path('redirect/', role_redirect, name='role_redirect'),

    path("manager/", manager_dashboard, name="manager_dashboard"),
    path('admin-panel/', admin_dashboard, name='admin_dashboard'),
    path("users/", views.user_list, name="accounts_users"),
    path("users/create/", views.user_create, name="accounts_user_create"),
    path("manager/restaurant-orders/", manager_restaurant_orders_today),
    path("manager/room-activity/", manager_room_activity_today),
    path("manager/payments/", manager_payments_today),

]
