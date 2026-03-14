from django.urls import path
from . import views

urlpatterns = [
    path("", views.store_dashboard, name="store_dashboard"),
    path("issue/", views.issue_stock, name="store_issue_stock"),

    path("request/",views.request_low_stock,name="store_request_low_stock"),
    path("movements/",views.stock_movement_log,name="store_movement_log"),
    path("request/", views.request_low_stock, name="store_request_stock"),
    path("requests/", views.my_stock_requests, name="store_requests"),
    path("requests/<int:pk>/", views.stock_request_detail, name="store_request_detail"),
    path("transfers/<int:pk>/",views.transfer_detail,name="store_transfer_detail"),
    path("ingredient-requests/",views.store_ingredient_requests,name="store_ingredient_requests"),
    path("requests/item/<int:item_id>/issue/",views.issue_request_item,name="issue_request_item"),
    path("history/issues/", views.store_issue_history, name="store_issue_history"),
    path("history/receipts/", views.store_receive_history, name="store_receive_history"),

]
