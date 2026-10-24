from django.urls import path

from .setup_views import linen_category_setup, linen_room_setup
from .views import (
    linen_dashboard,
    linen_receive,
    linen_issue,
)


urlpatterns = [
    path("", linen_dashboard, name="linen_dashboard"),
    path("receive/", linen_receive, name="linen_receive"),
    path("issue/", linen_issue, name="linen_issue"),
    path("setup/categories/", linen_category_setup, name="linen_category_setup"),
    path("setup/rooms/<int:pk>/", linen_room_setup, name="linen_room_setup"),
]
