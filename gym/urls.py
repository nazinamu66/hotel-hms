from django.urls import path

from . import views

urlpatterns = [
    path("", views.gym_home, name="gym_home"),
    path("members/new/", views.gym_member_create, name="gym_member_create"),
    path("members/<int:member_id>/", views.gym_member_detail, name="gym_member_detail"),
    path("plans/", views.gym_plan_setup, name="gym_plan_setup"),
    path("plans/<int:plan_id>/update/", views.gym_plan_update, name="gym_plan_update"),
    path("gate/", views.gym_gate_verify, name="gym_gate_verify"),
]
