from django.urls import path
from .views import (
    pos_v2,
    cart_add,
    cart_update,
    cart_clear,
    pos_commit,
    pos_order_list,
    pos_order_detail,
    refund_order,
)

urlpatterns = [
    path("pos/", pos_v2, name="restaurant_pos"),
    path("pos-v2/", pos_v2, name="restaurant_pos_v2"),
    path("cart/add/<int:item_id>/", cart_add, name="cart_add"),
    path("cart/update/<int:item_id>/", cart_update, name="cart_update"),
    path("cart/clear/", cart_clear, name="cart_clear"),
    path("pos/commit/", pos_commit, name="restaurant_pos_commit"),
    path("orders/", pos_order_list, name="restaurant_order_list"),
    path("orders/<int:order_id>/", pos_order_detail, name="restaurant_order_detail"),
    path("orders/<int:order_id>/refund/",refund_order, name="restaurant_order_refund"),

]
