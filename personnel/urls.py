from django.urls import path
from . import views

app_name = "personnel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("rosters/create/", views.roster_create, name="roster_create"),
    path("rosters/<int:roster_id>/", views.roster_detail, name="roster_detail"),
    path("rosters/<int:roster_id>/submit/", views.roster_submit, name="roster_submit"),
    path("rosters/<int:roster_id>/approve/", views.roster_approve, name="roster_approve"),
    path("rosters/<int:roster_id>/return/", views.roster_return, name="roster_return"),
    path("shifts/", views.shift_list, name="shifts"),
]
