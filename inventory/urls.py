from django.urls import path
from . import views

urlpatterns = [

    # PRODUCTS
    path("products/", views.product_list, name="product_list"),
    path("products/create/", views.product_create, name="product_create"),

    # SUPPLIERS
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("suppliers/create/", views.supplier_create, name="supplier_create"),

    # PURCHASE ORDERS
    path("purchase-orders/", views.po_list, name="po_list"),
    path("purchase-orders/create/", views.po_create, name="po_create"),
    path("purchase-orders/<int:pk>/", views.po_detail, name="po_detail"),

    path("purchase-orders/<int:pk>/submit/", views.po_submit, name="po_submit"),
    path("purchase-orders/<int:pk>/finalize/", views.po_finalize, name="po_finalize"),
    path("purchase-orders/<int:pk>/pay/", views.po_pay, name="po_pay"),
    path("purchase-orders/<int:pk>/receive/", views.po_receive_store, name="po_receive_store"),

    path("store/incoming/", views.store_incoming_pos, name="store_incoming_pos"),

    path("manager/requests/", views.manager_stock_requests, name="manager_stock_requests"),
    path("manager/requests/<int:pk>/review/", views.review_stock_request, name="manager_review_request"),

    path("incoming/<int:pk>/", views.incoming_delivery_detail, name="incoming_delivery_detail"),
    path("hotel/features/",views.hotel_feature_setup,name="hotel_feature_setup"),

    # path("foods/create/", views.prepared_food_create, name="kitchen_food_create"),
    path("foods/<int:food_id>/recipe/", views.recipe_edit, name="kitchen_recipe_edit"),
    path("recipe/item/<int:item_id>/delete/", views.recipe_item_delete, name="kitchen_recipe_item_delete"),
    path("recipe/<int:recipe_id>/item/add/", views.recipe_item_add, name="kitchen_recipe_item_add"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
]