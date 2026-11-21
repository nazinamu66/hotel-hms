from django.urls import path
from .views import restaurant_daily_report, restaurant_end_of_shift, restaurant_management_overview, restaurant_sales_performance
from . import views


urlpatterns = [
    path("restaurant/management/", restaurant_management_overview, name="restaurant_management_overview"),
    path("housekeeping/management/", views.housekeeping_management_overview, name="housekeeping_management_overview"),
    path("maintenance/management/", views.maintenance_management_overview, name="maintenance_management_overview"),
    path("maintenance/management/work-orders/", views.maintenance_work_order_analysis, name="maintenance_work_order_analysis"),
    path("maintenance/management/costs/", views.maintenance_cost_analysis, name="maintenance_cost_analysis"),
    path("maintenance/management/assets/", views.maintenance_asset_performance, name="maintenance_asset_performance"),
    path("maintenance/management/preventive-compliance/", views.maintenance_preventive_compliance, name="maintenance_preventive_compliance"),
    path("maintenance/management/suppliers/", views.maintenance_supplier_analysis, name="maintenance_supplier_analysis"),
    path("laundry/management/", views.laundry_management_overview, name="laundry_management_overview"),
    path("laundry/management/production/", views.laundry_production_analysis, name="laundry_production_analysis"),
    path("laundry/management/linen/", views.laundry_linen_movement_analysis, name="laundry_linen_movement_analysis"),
    path("laundry/management/costs/", views.laundry_cost_analysis, name="laundry_cost_analysis"),
    path("laundry/management/service/", views.laundry_service_quality_analysis, name="laundry_service_quality_analysis"),
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
