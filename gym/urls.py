from django.urls import path

from . import views

urlpatterns = [
    path("", views.gym_home, name="gym_home"),
    path("members/new/", views.gym_member_create, name="gym_member_create"),
    path("members/<int:member_id>/", views.gym_member_detail, name="gym_member_detail"),
    path("members/<int:member_id>/membership/new/", views.gym_membership_checkout, name="gym_membership_checkout"),
    path("memberships/<int:membership_id>/receipt/", views.gym_membership_receipt, name="gym_membership_receipt"),
    path("memberships/<int:membership_id>/pass/", views.gym_membership_pass, name="gym_membership_pass"),
    path("plans/", views.gym_plan_setup, name="gym_plan_setup"),
    path("plans/<int:plan_id>/update/", views.gym_plan_update, name="gym_plan_update"),
    path("gate/", views.gym_gate_verify, name="gym_gate_verify"),
]
