from django.urls import path
from .views import store_dashboard, issue_to_kitchen

urlpatterns = [
    path("", store_dashboard, name="store_dashboard"),
    path("issue/", issue_to_kitchen, name="store_issue"),
]
