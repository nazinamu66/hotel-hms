from django.urls import path

from . import views


app_name = "wifi"


urlpatterns = [

    path(
        "profiles/",
        views.profile_list,
        name="profile_list",
    ),

    path(
        "profiles/create/",
        views.profile_create,
        name="profile_create",
    ),

    path(
        "profiles/<int:profile_id>/edit/",
        views.profile_edit,
        name="profile_edit",
    ),

    path(
        "profiles/<int:profile_id>/toggle/",
        views.profile_toggle,
        name="profile_toggle",
    ),

    path(
        "accounts/",
        views.account_list,
        name="account_list",
    ),

    path(
        "accounts/create/",
        views.account_create,
        name="account_create",
    ),

    path(
        "accounts/<int:account_id>/",
        views.account_detail,
        name="account_detail",
    ),

    path(
        "devices/",
        views.device_list,
        name="device_list",
    ),

    path(
        "devices/create/",
        views.device_create,
        name="device_create",
    ),

    path(
        "devices/<int:device_id>/edit/",
        views.device_edit,
        name="device_edit",
    ),

    path(
        "devices/<int:device_id>/toggle/",
        views.device_toggle,
        name="device_toggle",
    ),

    path(
    "vouchers/",
    views.voucher_list,
    name="voucher_list",
),

    path(
        "vouchers/create/",
        views.voucher_create,
        name="voucher_create",
    ),

    path(
        "vouchers/<int:voucher_id>/",
        views.voucher_detail,
        name="voucher_detail",
    ),
    path(
        "vouchers/<int:voucher_id>/revoke/",
        views.voucher_revoke,
        name="voucher_revoke",
    ),

]