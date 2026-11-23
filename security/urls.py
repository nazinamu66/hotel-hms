from django.urls import path
from . import views

urlpatterns = [
    path("", views.security_dashboard, name="security_dashboard"),
    path("vehicles/", views.vehicle_visit_list, name="security_vehicle_list"),
    path("vehicles/entry/", views.vehicle_visit_create, name="security_vehicle_entry"),
    path("vehicles/<int:visit_id>/exit/", views.vehicle_visit_exit, name="security_vehicle_exit"),
    path("property-passes/", views.property_pass_list, name="security_pass_list"),
    path("property-passes/create/", views.property_pass_create, name="security_pass_create"),
    path("property-passes/<int:pass_id>/", views.property_pass_detail, name="security_pass_detail"),
    path("property-passes/<int:pass_id>/gate-out/", views.property_pass_gate_out, name="security_pass_gate_out"),
    path("property-passes/<int:pass_id>/return/", views.property_pass_return, name="security_pass_return"),
]
