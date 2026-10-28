from django.urls import path
from inventory.views import (
    department_request_stock,
    department_stock_requests,
)
from .views import (
    pos_v2,
    cart_add,
    cart_update,
    cart_clear,
    pos_commit,
    pos_order_list,
    pos_order_detail,
    pos_order_receipt,
    open_shift,
    close_shift,
    shift_list,
    shift_detail,
    table_list,
    restaurant_select_table,
    ready_food_alerts,
    ready_food_ticket_print,
)

urlpatterns = [
    path("pos/", pos_v2, name="restaurant_pos"),
    path("ready-food-alerts/", ready_food_alerts, name="restaurant_ready_food_alerts"),
    path("ready-food/<int:ticket_id>/print/", ready_food_ticket_print, name="restaurant_ready_food_ticket_print"),
    path("pos-v2/", pos_v2, name="restaurant_pos_v2"),
    path("cart/add/<int:item_id>/", cart_add, name="cart_add"),
    path("cart/update/<int:item_id>/", cart_update, name="cart_update"),
    path("cart/clear/", cart_clear, name="cart_clear"),
    path("pos/commit/", pos_commit, name="restaurant_pos_commit"),
    path("orders/", pos_order_list, name="restaurant_order_list"),
    path("orders/<int:order_id>/receipt/", pos_order_receipt, name="restaurant_order_receipt"),
    path("orders/<int:order_id>/", pos_order_detail, name="restaurant_order_detail"),
    path("shift/open/", open_shift, name="restaurant_shift_open"),
    path("shift/close/", close_shift, name="restaurant_shift_close"),
    path("shifts/", shift_list, name="restaurant_shift_list"),
    path("shifts/<int:shift_id>/", shift_detail, name="restaurant_shift_detail"),
    path("tables/", table_list, name="restaurant_table_list"),
    path("tables/<int:table_id>/select/", restaurant_select_table, name="restaurant_select_table"),

    path(
        "stock/request/",
        department_request_stock,
        name="restaurant_request_stock",
    ),
    path(
        "stock/requests/",
        department_stock_requests,
        name="restaurant_stock_requests",
    ),





]
