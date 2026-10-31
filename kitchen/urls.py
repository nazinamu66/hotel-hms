from django.urls import path
from . import views

urlpatterns = [
    path("", views.kitchen_dashboard, name="kitchen_dashboard"),
    path("handover/", views.kitchen_handover_list, name="kitchen_handover_list"),
    path("handover/new/", views.kitchen_handover_create, name="kitchen_handover_create"),
    path("handover/<int:pk>/", views.kitchen_handover_detail, name="kitchen_handover_detail"),
    path("storage-register/", views.kitchen_storage_register, name="kitchen_storage_register"),
    path("food-sales/", views.kitchen_food_sales_report, name="kitchen_food_sales_report"),
    path("management-reports/", views.kitchen_management_report, name="kitchen_management_report"),
    path("management-reports/spoilage/", views.kitchen_spoilage_report, name="kitchen_spoilage_report"),
    path("management-reports/inventory/", views.kitchen_inventory_report, name="kitchen_inventory_report"),
    path("management-reports/batch-profitability/", views.kitchen_batch_profitability_report, name="kitchen_batch_profitability_report"),
    path("management-reports/purchasing/", views.kitchen_purchasing_report, name="kitchen_purchasing_report"),

    # Ingredients
    path("ingredients/", views.kitchen_ingredients, name="kitchen_ingredients"),
    path("spoilage/", views.kitchen_spoilage, name="kitchen_spoilage"),
    path("spoilage/<int:pk>/review/", views.kitchen_spoilage_review, name="kitchen_spoilage_review"),

    # Ingredient Requests
    path("ingredients/request/", views.kitchen_ingredient_request_create, name="kitchen_ingredient_request_create"),
    path("ingredients/requests/", views.kitchen_ingredient_requests, name="kitchen_ingredient_requests"),
    path("ingredients/request/<int:pk>/", views.kitchen_ingredient_request_detail, name="kitchen_ingredient_request_detail"),

    # Temporary redirect shim (can be removed later)
    path("ingredients/request/<int:pk>/receive/",views.kitchen_confirm_direct_ingredients,name="kitchen_confirm_direct_ingredients"),

    # Manager
    path("manager/ingredient-requests/",views.manager_ingredient_requests,name="manager_ingredient_requests"),

    # Production
    path("produce/quick/", views.kitchen_quick_production, name="kitchen_quick_production"),
    path("recipe/<int:recipe_id>/ingredients/", views.recipe_ingredients_api),

    # Recipes
    path("foods/", views.prepared_food_list, name="kitchen_food_list"),
    path("recipes/<int:recipe_id>/versions/", views.recipe_version_history, name="kitchen_recipe_version_history"),
    path("recipes/<int:recipe_id>/versions/draft/", views.recipe_version_draft_create, name="kitchen_recipe_version_draft_create"),
    path("recipe-versions/<int:version_id>/items/<int:item_id>/update/", views.recipe_version_item_update, name="kitchen_recipe_version_item_update"),
    path("recipe-versions/<int:version_id>/items/add/", views.recipe_version_item_add, name="kitchen_recipe_version_item_add"),
    path("recipe-versions/<int:version_id>/items/<int:item_id>/remove/", views.recipe_version_item_remove, name="kitchen_recipe_version_item_remove"),
    path("recipe-versions/<int:version_id>/approve/", views.recipe_version_approve, name="kitchen_recipe_version_approve"),

    path("incoming-deliveries/", views.kitchen_incoming_deliveries, name="kitchen_incoming_deliveries"),
    path("urgent-purchase/new/", views.urgent_purchase_create, name="urgent_purchase_create"),
    path("urgent-purchases/history/", views.urgent_purchase_history, name="urgent_purchase_history"),
    path("urgent-purchases/review/", views.urgent_purchase_review, name="urgent_purchase_review"),
    path("urgent-purchases/<int:pk>/reconcile/", views.urgent_purchase_reconcile, name="urgent_purchase_reconcile"),
    path("direct-purchases/",views.direct_purchase_list,name="direct_purchase_list"),
    path("direct-purchase/<int:pk>/", views.direct_purchase_detail, name="direct_purchase_detail"),
    path("direct-purchase/<int:pk>/pay/", views.direct_purchase_pay, name="direct_purchase_pay"),
    path("direct-purchase/<int:pk>/receive/", views.direct_purchase_receive, name="direct_purchase_receive"),


    path("production/history/", views.production_history, name="kitchen_production_history"),
    path("production/overrides/", views.production_override_queue, name="production_override_queue"),
    path("production/<int:pk>/override/approve/", views.production_override_approve, name="production_override_approve"),
    path("production/<int:pk>/override/reject/", views.production_override_reject, name="production_override_reject"),
    path("production/<int:pk>/", views.production_detail, name="kitchen_production_detail"),

    path("ticket/<int:ticket_id>/start/",views.kitchen_ticket_start,name="kitchen_ticket_start"),
    path("ticket/<int:ticket_id>/ready/",views.kitchen_ticket_ready,name="kitchen_ticket_ready"),
    path("ticket/<int:ticket_id>/served/",views.kitchen_ticket_served,name="kitchen_ticket_served"),


]
