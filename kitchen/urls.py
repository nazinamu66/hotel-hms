from django.urls import path
from . import views

urlpatterns = [
    path("", views.kitchen_dashboard, name="kitchen_dashboard"),

    # Ingredients
    path("ingredients/", views.kitchen_ingredients, name="kitchen_ingredients"),

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

    path("direct-purchases/",views.direct_purchase_list,name="direct_purchase_list"),
    path("direct-purchase/<int:pk>/", views.direct_purchase_detail, name="direct_purchase_detail"),
    path("direct-purchase/<int:pk>/pay/", views.direct_purchase_pay, name="direct_purchase_pay"),
    path("direct-purchase/<int:pk>/receive/", views.direct_purchase_receive, name="direct_purchase_receive"),


    path("production/history/", views.production_history, name="kitchen_production_history"),
    path("production/<int:pk>/", views.production_detail, name="kitchen_production_detail"),

    path("ticket/<int:ticket_id>/start/",views.kitchen_ticket_start,name="kitchen_ticket_start"),
    path("ticket/<int:ticket_id>/ready/",views.kitchen_ticket_ready,name="kitchen_ticket_ready"),
    path("ticket/<int:ticket_id>/served/",views.kitchen_ticket_served,name="kitchen_ticket_served"),


]
