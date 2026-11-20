from django.urls import path
from .views import restaurant_daily_report, restaurant_end_of_shift, restaurant_management_overview, restaurant_sales_performance
from . import views


urlpatterns = [
    path("restaurant/management/", restaurant_management_overview, name="restaurant_management_overview"),
    path("maintenance/management/", views.maintenance_management_overview, name="maintenance_management_overview"),
    path("maintenance/management/work-orders/", views.maintenance_work_order_analysis, name="maintenance_work_order_analysis"),
    path("restaurant/management/inventory/", views.restaurant_inventory_status, name="restaurant_inventory_status"),
    path("restaurant/management/spoilage/", views.restaurant_spoilage_analysis, name="restaurant_spoilage_analysis"),
    path("restaurant/management/spoilage/records/", views.restaurant_spoilage_records, name="restaurant_spoilage_records"),
    path("restaurant/management/spoilage/records/<int:pk>/", views.restaurant_spoilage_record_detail, name="restaurant_spoilage_record_detail"),
    path("restaurant/management/sales/", restaurant_sales_performance, name="restaurant_sales_performance"),
    path("restaurant/daily/", restaurant_management_overview, name="restaurant_daily_report"),
    path("restaurant/end-of-shift/", restaurant_management_overview, name="restaurant_end_of_shift"),
    path("daily-stock/",views.daily_stock_report,name="daily_stock_report"),
    path("department-consumption/",views.department_consumption_report,name="department_consumption_report"),
    path("owner/dashboard/", views.owner_dashboard, name="owner_dashboard"),
    # path("hotels/",views.hotel_list,name="hotel_list"),
    # path("departments/",views.department_list,name="department_list"),
    # # path("suppliers/",views.supplier_list,name="supplier_list"),
    # # path("products/",views.product_list,name="product_list"),
    # path("hotels/create/",views.hotel_create,name="hotel_create"),
    # path("departments/create/",views.department_create,name="department_create"),
    # path("departments/<int:dept_id>/edit/", views.department_edit, name="department_edit"),
]
