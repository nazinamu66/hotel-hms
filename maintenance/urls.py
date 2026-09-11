from django.urls import path
from .views import (
    create_ticket,
    maintenance_dashboard,
    maintenance_ticket_detail,
    resolve_ticket,
    start_ticket,
    create_ticket_room,
    add_labour,
    add_material,
    maintenance_work_orders,
    my_work_orders,
    maintenance_work_history,
    maintenance_stock,
    maintenance_stock_movements,
    maintenance_assets,
    maintenance_asset_create,
    maintenance_schedules,
    maintenance_schedule_create,
    maintenance_schedule_work_order,
    maintenance_products,
    maintenance_product_create,
    maintenance_suppliers,
    maintenance_supplier_create,
    request_maintenance_stock,
    my_maintenance_stock_requests,
)
from inventory.views import (
    department_request_stock,
    department_stock_requests,
    department_incoming_pos
)
urlpatterns = [

    path(
        "",
        maintenance_dashboard,
        name="maintenance_dashboard",
    ),

    path(
        "create/<int:room_id>/",
        create_ticket,
        name="maintenance_create_ticket",
    ),
    path(
        "create/",
        create_ticket_room,
        name="maintenance_create_ticket_room",
    ),

    path(
        "products/",
        maintenance_products,
        name="maintenance_products",
    ),

    path(
        "suppliers/",
        maintenance_suppliers,
        name="maintenance_suppliers",
    ),

    path(
        "suppliers/create/",
        maintenance_supplier_create,
        name="maintenance_supplier_create",
    ),

    path(
        "products/create/",
        maintenance_product_create,
        name="maintenance_product_create",
    ),
    path(
        "schedules/",
        maintenance_schedules,
        name="maintenance_schedules",
    ),

    path(
        "schedules/create/",
        maintenance_schedule_create,
        name="maintenance_schedule_create",
    ),
    
    path(
        "schedules/<int:schedule_id>/work-order/",
        maintenance_schedule_work_order,
        name="maintenance_schedule_work_order",
    ),

    path(
        "work-orders/",
        maintenance_work_orders,
        name="maintenance_work_orders",
    ),

    path(
        "my-work-orders/",
        my_work_orders,
        name="maintenance_my_work_orders",
    ),

    path(
        "history/",
        maintenance_work_history,
        name="maintenance_work_history",
    ),

    path(
        "assets/",
        maintenance_assets,
        name="maintenance_assets",
    ),

    path(
        "assets/create/",
        maintenance_asset_create,
        name="maintenance_asset_create",
    ),

    path(
        "ticket/<int:ticket_id>/materials/add/",
        add_material,
        name="maintenance_add_material",
    ),
    path(
        "ticket/<int:ticket_id>/labour/add/",
        add_labour,
        name="maintenance_add_labour",
    ),

    path(
        "stock/",
        maintenance_stock,
        name="maintenance_stock",
    ),
    path(
        "stock/movements/",
        maintenance_stock_movements,
        name="maintenance_stock_movements",
    ),
    
    path(
        "ticket/<int:ticket_id>/",
        maintenance_ticket_detail,
        name="maintenance_ticket_detail",
    ),

    path(
        "start/<int:ticket_id>/",
        start_ticket,
        name="maintenance_start_ticket",
    ),

    path(
        "resolve/<int:ticket_id>/",
        resolve_ticket,
        name="maintenance_resolve_ticket",
    ),

    path(
        "stock/request/",
        department_request_stock,
        name="maintenance_request_stock",
    ),

    path(
        "stock/requests/",
        department_stock_requests,
        name="maintenance_stock_requests",
    ),
    path(
        "stock/incoming/",
        department_incoming_pos,
        name="maintenance_incoming_pos",
    ),

]