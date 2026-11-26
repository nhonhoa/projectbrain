from django.urls import path

from hub_ui import views

app_name = "hub_ui"

urlpatterns = [
    path("showcase/", views.showcase, name="showcase"),
]
