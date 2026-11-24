from django.urls import path
from . import views

urlpatterns = [
    path("", views.security_dashboard, name="security_dashboard"),
    path("handovers/", views.shift_handover_list, name="security_handover_list"),
    path("handovers/create/", views.shift_handover_create, name="security_handover_create"),
    path("handovers/<int:handover_id>/", views.shift_handover_detail, name="security_handover_detail"),
    path("handovers/<int:handover_id>/acknowledge/", views.shift_handover_acknowledge, name="security_handover_acknowledge"),
    path("handovers/<int:handover_id>/cso-review/", views.shift_handover_cso_review, name="security_handover_cso_review"),
    path("visitors/", views.visitor_visit_list, name="security_visitor_list"),
    path("visitors/entry/", views.visitor_visit_create, name="security_visitor_entry"),
    path("visitors/<int:visit_id>/exit/", views.visitor_visit_exit, name="security_visitor_exit"),
    path("vehicles/", views.vehicle_visit_list, name="security_vehicle_list"),
    path("vehicles/entry/", views.vehicle_visit_create, name="security_vehicle_entry"),
    path("vehicles/<int:visit_id>/exit/", views.vehicle_visit_exit, name="security_vehicle_exit"),
    path("property-passes/", views.property_pass_list, name="security_pass_list"),
    path("property-passes/create/", views.property_pass_create, name="security_pass_create"),
    path("property-passes/<int:pass_id>/", views.property_pass_detail, name="security_pass_detail"),
    path("property-passes/<int:pass_id>/gate-out/", views.property_pass_gate_out, name="security_pass_gate_out"),
    path("property-passes/<int:pass_id>/return/", views.property_pass_return, name="security_pass_return"),
]
