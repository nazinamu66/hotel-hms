from django.urls import path

from .setup_views import linen_category_setup, linen_room_setup
from .room_opening_views import room_linen_opening
from .views import (
    linen_dashboard,
    linen_receive,
    linen_issue,
)
from .reconciliation_views import linen_reconciliation
from .custody_adjustment_views import linen_custody_adjustment


urlpatterns = [
    path("", linen_dashboard, name="linen_dashboard"),
    path("reconciliation/", linen_reconciliation, name="linen_reconciliation"),
    path("reconciliation/custody/<int:pk>/adjust/", linen_custody_adjustment, name="linen_custody_adjustment"),
    path("receive/", linen_receive, name="linen_receive"),
    path("issue/", linen_issue, name="linen_issue"),
    path("setup/categories/", linen_category_setup, name="linen_category_setup"),
    path("setup/room-opening/", room_linen_opening, name="linen_room_opening"),
    path("setup/rooms/<int:pk>/", linen_room_setup, name="linen_room_setup"),
]
