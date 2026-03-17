from django.urls import path
from .views import restaurant_daily_report, restaurant_end_of_shift
from . import views


urlpatterns = [
    path("restaurant/daily/", restaurant_daily_report, name="restaurant_daily_report"),
    path("restaurant/end-of-shift/",restaurant_end_of_shift,name="restaurant_end_of_shift"),
    path("daily-stock/",views.daily_stock_report,name="daily_stock_report"),
    path("department-consumption/",views.department_consumption_report,name="department_consumption_report"),
    path("owner/dashboard/", views.owner_dashboard, name="owner_dashboard"),
    path("hotels/",views.hotel_list,name="hotel_list"),
    path("departments/",views.department_list,name="department_list"),
    # path("suppliers/",views.supplier_list,name="supplier_list"),
    # path("products/",views.product_list,name="product_list"),
    path("hotels/create/",views.hotel_create,name="hotel_create"),
    path("departments/create/",views.department_create,name="department_create"),
    path("departments/<int:dept_id>/edit/", views.department_edit, name="department_edit"),
]
