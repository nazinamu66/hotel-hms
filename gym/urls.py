from django.urls import path

from . import views

urlpatterns = [
    path("", views.gym_home, name="gym_home"),
]