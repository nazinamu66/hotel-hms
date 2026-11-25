from django.urls import path
from . import views, employee_records, action_views, separation_views

app_name = "personnel"

urlpatterns = [
    path("separations/", separation_views.separation_list, name="separation_list"),
    path("separations/<int:clearance_id>/", separation_views.separation_detail, name="separation_detail"),
    path("separations/<int:clearance_id>/verify/", separation_views.separation_verify, name="separation_verify"),
    path("separations/<int:clearance_id>/submit/", separation_views.separation_submit, name="separation_submit"),
    path("separations/<int:clearance_id>/decision/<str:decision>/", separation_views.separation_decision, name="separation_decision"),
    path("actions/", action_views.action_list, name="action_list"),
    path("actions/review/", action_views.action_approval_queue, name="action_approval_queue"),
    path("actions/create/", action_views.action_create, name="action_create"),
    path("actions/<int:action_id>/", action_views.action_detail, name="action_detail"),
    path("actions/<int:action_id>/submit/", action_views.action_submit, name="action_submit"),
    path("actions/<int:action_id>/decision/<str:decision>/", action_views.action_decide, name="action_decide"),
    path("", views.dashboard, name="dashboard"),
    path("rosters/", views.roster_dashboard, name="roster_dashboard"),
    path("employees/", employee_records.employee_list, name="employee_list"),
    path("employees/create/", employee_records.employee_create, name="employee_create"),
    path("employees/<int:employee_id>/", employee_records.employee_detail, name="employee_detail"),
    path("employees/<int:employee_id>/edit/", employee_records.employee_edit, name="employee_edit"),
    path("employees/<int:employee_id>/documents/upload/", employee_records.employee_document_upload, name="employee_document_upload"),
    path("documents/<int:document_id>/download/", employee_records.employee_document_download, name="employee_document_download"),
    path("rosters/create/", views.roster_create, name="roster_create"),
    path("rosters/<int:roster_id>/", views.roster_detail, name="roster_detail"),
    path("rosters/<int:roster_id>/submit/", views.roster_submit, name="roster_submit"),
    path("rosters/<int:roster_id>/approve/", views.roster_approve, name="roster_approve"),
    path("rosters/<int:roster_id>/return/", views.roster_return, name="roster_return"),
    path("shifts/", views.shift_list, name="shifts"),
    path("reconciliations/<int:reconciliation_id>/", views.roster_reconciliation_detail, name="reconciliation_detail"),
]
